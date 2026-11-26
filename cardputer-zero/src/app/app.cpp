/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

#include "app.h"

#include "asset_manager.h"
#include "base_viewmodel.h"
#include "engine.h"
#include "help_popup.h"
#include "linux_input.h"
#include "logger.h"
#include "screen_manager.h"
#include "screenshot_service.h"
#include "settings.h"
#include "theme.h"
#include "toast.h"

#include <csignal>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>

#if USE_DESKTOP
#include "desktop_simulator_frame.h"
#endif

#if !USE_DESKTOP
#if APP_USE_DRM
#include "src/drivers/display/drm/lv_linux_drm.h"
#else
#include "src/drivers/display/fb/lv_linux_fbdev.h"
#endif
#endif

#ifndef APP_FRAMEBUFFER_DEVICE
#define APP_FRAMEBUFFER_DEVICE "/dev/fb0"
#endif

#ifndef APP_DRM_DEVICE
#define APP_DRM_DEVICE "/dev/dri/card0"
#endif

#ifndef APP_DRM_CONNECTOR_ID
#define APP_DRM_CONNECTOR_ID -1
#endif

#ifndef APP_CONFIG_FILE
#define APP_CONFIG_FILE "meshhunter.conf"
#endif

namespace app {
namespace {

// APPLaunch stops apps with SIGTERM and SIGKILLs them ~3 s later, so the
// handler only flips a flag; the main loop then stops the engine, which
// flushes the node CSVs and releases the radio well inside that window.
volatile std::sig_atomic_t g_signal_quit = 0;

void on_signal(int) { g_signal_quit = 1; }

void quit_requested_observer(lv_observer_t* observer, lv_subject_t* subject) {
    auto* running = static_cast<bool*>(lv_observer_get_user_data(observer));
    if (running && lv_subject_get_int(subject)) {
        *running = false;
    }
}

void tick_timer_cb(lv_timer_t* timer) {
    auto* view_model = static_cast<viewmodel::BaseViewModel*>(lv_timer_get_user_data(timer));
    if (view_model) view_model->tick();
}

mesh::Settings load_all_settings(const std::string& user_path) {
    mesh::Settings settings;
    std::string error;
    // Packaged defaults, then the user's own copy on top.
    if (load_settings(APP_CONFIG_FILE, settings, error)) {
        LOG_INFO("loaded config: {}", APP_CONFIG_FILE);
    } else {
        LOG_WARN("no system config {}: {}", APP_CONFIG_FILE, error);
    }
    std::error_code fs_error;
    if (std::filesystem::is_regular_file(user_path, fs_error)) {
        if (load_settings(user_path, settings, error)) {
            LOG_INFO("loaded config: {}", user_path);
        } else {
            LOG_WARN("failed to load {}: {}", user_path, error);
        }
    }
    // Development convenience: MESHHUNTER_REPLAY=<sniffer_packets.jsonl>
    // replays captured traffic instead of opening the radio.
    if (const char* replay = std::getenv("MESHHUNTER_REPLAY"); replay && *replay) {
        settings.source = mesh::SourceKind::Replay;
        settings.replay_file = replay;
    }
#if USE_DESKTOP
    if (settings.source == mesh::SourceKind::CapLora) settings.source = mesh::SourceKind::None;
    settings.gps_enabled = settings.gps_enabled && std::getenv("MESHHUNTER_GPS") != nullptr;
    if (const char* gps = std::getenv("MESHHUNTER_GPS"); gps && *gps) settings.gps_device = gps;
#endif
    return settings;
}

class ShortcutController {
public:
    ShortcutController(viewmodel::BaseViewModel& view_model, AssetManager& assets)
        : view_model_(view_model),
          toast_(std::make_unique<view::widgets::Toast>(lv_layer_top(), view_model, assets)),
          help_popup_(std::make_unique<view::widgets::HelpPopup>(lv_layer_top(), view_model, assets)) {
        toast_->build();
        help_popup_->build();
        platform::set_global_key_listener(global_key_listener, this);
        platform::set_key_release_listener(key_release_listener, this);
    }

    ~ShortcutController() {
        platform::clear_key_release_listener(key_release_listener, this);
        platform::clear_global_key_listener(global_key_listener, this);
    }

private:
    bool handle_key(uint32_t key, bool long_pressed) {
        if (key == platform::kKeyPrintScreen) {
            if (!long_pressed) {
                const auto result = platform::screenshot::capture_active_screen();
                if (result.success) {
                    LOG_INFO("screenshot saved: {}", result.path);
                    toast_->show_highlighted("Saved to ", result.path, "", view::widgets::ToastTone::Success, 5200,
                                             true);
                } else {
                    LOG_WARN("screenshot failed: {}", result.error);
                    toast_->show("Screenshot failed: " + result.error, view::widgets::ToastTone::Error, 3200);
                }
            }
            return true;
        }

        if (key == platform::kKeyHelp || (key == 'h' && !long_pressed && !help_popup_->visible())) {
            if (!long_pressed) {
                toast_->hide();
                if (help_popup_->visible()) {
                    help_popup_->hide();
                } else {
                    help_popup_->show(view_model_.current_page());
                }
            }
            return true;
        }

        if (help_popup_->visible()) {
            if ((key == LV_KEY_ESC || key == 'h') && !long_pressed) {
                help_popup_->hide();
            }
            return true;
        }

        if (key == LV_KEY_ESC) {
            if (long_pressed) {
                if (exit_armed_) {
                    toast_->hide();
                    view_model_.request_quit();
                }
                return true;
            }
            // ESC first closes a detail view; otherwise it arms exit.
            if (view_model_.handle_key(key)) return true;
            exit_armed_ = true;
            toast_->show_persistent_highlighted("Hold ", "ESC", " to exit", view::widgets::ToastTone::Warning);
            return true;
        }

        // Digits 4..8 are the nav bar keys: let the router dispatch them.
        if (key >= '4' && key <= '8') return false;

        return !long_pressed && view_model_.handle_key(key);
    }

    void handle_release(uint32_t key) {
        if (key == LV_KEY_ESC && exit_armed_) {
            toast_->hide();
            exit_armed_ = false;
        }
    }

    static bool global_key_listener(uint32_t key, const char*, bool long_pressed, void* user_data) {
        auto* controller = static_cast<ShortcutController*>(user_data);
        return controller && controller->handle_key(key, long_pressed);
    }

    static void key_release_listener(uint32_t key, const char*, void* user_data) {
        auto* controller = static_cast<ShortcutController*>(user_data);
        if (controller) {
            controller->handle_release(key);
        }
    }

    viewmodel::BaseViewModel& view_model_;
    std::unique_ptr<view::widgets::Toast> toast_;
    std::unique_ptr<view::widgets::HelpPopup> help_popup_;
    bool exit_armed_{false};
};

#if !USE_DESKTOP
lv_display_t* init_device_display() {
#if APP_USE_DRM
    auto* display = lv_linux_drm_create();
    if (!display) {
        return nullptr;
    }

    if (lv_linux_drm_set_file(display, APP_DRM_DEVICE, APP_DRM_CONNECTOR_ID) != LV_RESULT_OK) {
        lv_display_delete(display);
        return nullptr;
    }

    platform::init_key_input(display);
    return display;
#else
    auto* display = lv_linux_fbdev_create();
    if (!display) {
        return nullptr;
    }

    if (lv_linux_fbdev_set_file(display, APP_FRAMEBUFFER_DEVICE) != LV_RESULT_OK) {
        lv_display_delete(display);
        return nullptr;
    }

    platform::init_key_input(display);
    return display;
#endif
}
#endif

} // namespace

int Application::run() {
    logger::Logger::init();
    logger::Logger::set_tag("meshhunter");

    std::signal(SIGTERM, on_signal);
    std::signal(SIGINT, on_signal);

    lv_init();

    AssetManager assets;
    for (const auto& root : assets.roots()) {
        LOG_INFO("asset root: {}", root.string());
    }

    const std::string user_config_path = mesh::user_config_path();
    const mesh::Settings settings = load_all_settings(user_config_path);

    viewmodel::BaseViewModel view_model;
    view_model.set_dark_mode(settings.dark_mode);

#if USE_DESKTOP
    DesktopSimulatorFrame simulator_frame(assets);
    auto* display = simulator_frame.display();
#else
    auto* display = init_device_display();
#endif
    if (!display) {
        LOG_ERROR("failed to initialize display");
        return 1;
    }

    auto* standard_font = assets.load_standard_font(14);
    view::apply_lvgl_theme(display, view_model.is_dark_mode(), standard_font ? standard_font : LV_FONT_DEFAULT);

#if USE_DESKTOP
    simulator_frame.bind_dark_mode(view_model.dark_mode_subject());
#endif

    // Settings changed from the UI are written to the user's config.
    mesh::Engine engine(settings, [user_config_path](const mesh::Settings& s) {
        std::string error;
        if (mesh::save_settings(user_config_path, s, error)) {
            LOG_INFO("saved config: {}", user_config_path);
        } else {
            LOG_WARN("failed to save config {}: {}", user_config_path, error);
        }
    });
    engine.start();
    view_model.attach_engine(&engine);
    LOG_INFO("data dir: {}", engine.counters().data_dir);
    if (const auto note = engine.counters().storage_note; !note.empty()) LOG_INFO("{}", note);

    ScreenManager screen_manager(view_model, assets);
    screen_manager.start();
    ShortcutController shortcuts(view_model, assets);
    auto* tick_timer = lv_timer_create(tick_timer_cb, 200, &view_model);

    bool running = true;
    auto* quit_observer = lv_subject_add_observer(view_model.quit_requested_subject(), quit_requested_observer,
                                                  &running);

    LOG_INFO("LVGL app started at {}x{}", lv_display_get_horizontal_resolution(display),
             lv_display_get_vertical_resolution(display));
    while (running && !g_signal_quit
#if USE_DESKTOP
           && simulator_frame.process_events()
#endif
    ) {
        lv_timer_handler();
        lv_delay_ms(5);
    }

    LOG_INFO("shutting down");
    if (tick_timer) lv_timer_delete(tick_timer);
    if (quit_observer) {
        lv_observer_remove(quit_observer);
    }
    view_model.attach_engine(nullptr);
    engine.stop();
    LOG_INFO("stopped");

    return 0;
}

} // namespace app
