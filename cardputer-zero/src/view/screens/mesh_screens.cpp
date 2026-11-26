#include "mesh_screens.h"

#include "asset_manager.h"
#include "engine.h"
#include "theme.h"

#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <ctime>

namespace screen {
namespace {

constexpr int32_t kRowH = 15;

std::string fmt(const char* format, ...) __attribute__((format(printf, 1, 2)));
std::string fmt(const char* format, ...) {
    char buf[256];
    va_list args;
    va_start(args, format);
    std::vsnprintf(buf, sizeof(buf), format, args);
    va_end(args);
    return buf;
}

const char* kind_badge(const mesh::NodeView& n) {
    if (n.meshtastic) return "MT";
    if (n.kind == "Repeater") return "RP";
    if (n.kind == "Chat") return "CH";
    if (n.kind == "Room") return "RM";
    if (n.kind == "Sensor") return "SN";
    return "??";
}

lv_color_t kind_color(const mesh::NodeView& n, const view::ThemePalette& c) {
    if (n.meshtastic) return c.warning;
    if (n.kind == "Repeater") return c.info;
    if (n.kind == "Chat") return c.success;
    if (n.kind == "Room") return c.primary;
    return c.text_disabled;
}

const char* radio_state_text(mesh::RadioState s) {
    switch (s) {
        case mesh::RadioState::Stopped: return "Stopped";
        case mesh::RadioState::Starting: return "Starting";
        case mesh::RadioState::Listening: return "Listening";
        case mesh::RadioState::Error: return "Error";
    }
    return "";
}

void set_visible(lv_obj_t* obj, bool visible) {
    if (visible) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
}

}  // namespace

// ---------------------------------------------------------------------------

MeshScreen::MeshScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets)
    : BaseScreen(view_model, assets) {}

void MeshScreen::build_content(lv_obj_t* content) {
    font_ = assets().load_standard_font(12, app::StandardFontWeight::Regular);
    if (!font_) font_ = &lv_font_montserrat_12;
    build_page(content);
    // Observers die with the content object, so a screen switch cleans up.
    lv_subject_add_observer_obj(view_model().data_revision_subject(), refresh_cb, content, this);
    lv_subject_add_observer_obj(view_model().cursor_subject(), refresh_cb, content, this);
    lv_subject_add_observer_obj(view_model().dark_mode_subject(), refresh_cb, content, this);
}

void MeshScreen::refresh_cb(lv_observer_t* observer, lv_subject_t*) {
    auto* self = static_cast<MeshScreen*>(lv_observer_get_user_data(observer));
    if (self) self->refresh();
}

lv_obj_t* MeshScreen::make_label(lv_obj_t* parent, int32_t x, int32_t y, int32_t w, lv_text_align_t align) {
    auto* label = lv_label_create(parent);
    lv_label_set_text(label, "");
    lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
    lv_obj_set_width(label, w);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_style_text_font(label, font_, 0);
    lv_obj_set_style_text_align(label, align, 0);
    return label;
}

// ---------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------

NodesScreen::NodesScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets)
    : MeshScreen(view_model, assets) {
    init();
}

void NodesScreen::build_page(lv_obj_t* content) {
    header_ = make_label(content, 6, 1, 308, LV_TEXT_ALIGN_LEFT);
    rows_.build(content, 17, kRowH, 6,
                {{4, 22}, {28, 170}, {200, 12}, {212, 54, LV_TEXT_ALIGN_RIGHT}, {268, 46, LV_TEXT_ALIGN_RIGHT}},
                font());
    empty_ = lv_label_create(content);
    lv_obj_set_width(empty_, 300);
    lv_obj_set_style_text_font(empty_, font(), 0);
    lv_obj_set_style_text_align(empty_, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(empty_, LV_LABEL_LONG_WRAP);
    lv_obj_align(empty_, LV_ALIGN_CENTER, 0, 8);

    detail_ = lv_label_create(content);
    lv_obj_set_size(detail_, 320, 110);
    lv_obj_set_pos(detail_, 0, 0);
    lv_obj_set_style_pad_hor(detail_, 8, 0);
    lv_obj_set_style_pad_ver(detail_, 3, 0);
    lv_obj_set_style_bg_opa(detail_, LV_OPA_COVER, 0);
    lv_obj_set_style_text_font(detail_, font(), 0);
    lv_label_set_long_mode(detail_, LV_LABEL_LONG_CLIP);
    lv_obj_add_flag(detail_, LV_OBJ_FLAG_HIDDEN);
    refresh();
}

void NodesScreen::refresh() {
    auto* engine = view_model().engine();
    const auto colors = view::palette(view_model().is_dark_mode());
    rows_.apply_palette(colors);
    lv_obj_set_style_text_color(header_, colors.text_disabled, 0);
    lv_obj_set_style_text_color(empty_, colors.text_disabled, 0);
    lv_obj_set_style_text_color(detail_, colors.text, 0);
    lv_obj_set_style_bg_color(detail_, colors.surface, 0);
    if (!engine) return;

    const auto nodes = engine->nodes();
    const auto counters = engine->counters();
    lv_label_set_text(header_, fmt("Heard %zu   New %zu   Logged %zu   MT %zu", counters.heard_session,
                                   counters.new_session, counters.logged_meshcore, counters.known_meshtastic)
                                   .c_str());

    const int total = static_cast<int>(nodes.size());
    const int cursor = view_model().cursor(model::AppPage::Nodes);
    const std::time_t now = std::time(nullptr);

    if (total == 0) {
        const auto radio = engine->radio();
        std::string text = "Listening for nodes...";
        if (radio.state == mesh::RadioState::Error) text = "Radio: " + radio.message;
        else if (radio.state == mesh::RadioState::Starting) text = radio.message;
        lv_label_set_text(empty_, text.c_str());
        set_visible(empty_, true);
    } else {
        set_visible(empty_, false);
    }

    const int start = window_start(cursor, rows_.rows(), total);
    for (int r = 0; r < rows_.rows(); ++r) {
        const int idx = start + r;
        if (idx >= total) {
            rows_.set_row_visible(r, false);
            continue;
        }
        rows_.set_row_visible(r, true);
        const auto& n = nodes[static_cast<size_t>(idx)];
        rows_.set_text(r, 0, kind_badge(n));
        rows_.set_color(r, 0, kind_color(n, colors));
        rows_.set_text(r, 1, (n.is_new ? "* " : "") + n.name);
        if (n.is_new) rows_.set_color(r, 1, colors.success);
        rows_.set_text(r, 2, n.has_gps ? "@" : "");
        rows_.set_color(r, 2, colors.info);
        rows_.set_text(r, 3, n.has_signal ? fmt("%.0f", n.rssi) : "");
        rows_.set_text(r, 4, age_text(static_cast<long>(now - n.last_heard)));
        rows_.set_color(r, 4, colors.text_disabled);
        rows_.set_selected(r, idx == cursor, colors);
    }

    const bool show_detail = view_model().detail_open() && total > 0;
    set_visible(detail_, show_detail);
    if (show_detail) {
        const auto& n = nodes[static_cast<size_t>(std::min(cursor, total - 1))];
        std::string text = n.name + "\n" + n.kind + "  " + n.id + "\n";
        text += n.has_gps ? fmt("%.6f, %.6f\n", n.lat, n.lon) : std::string("No position advertised\n");
        text += n.has_signal ? fmt("RSSI %.1f dBm  SNR %.2f dB\n", n.rssi, n.snr) : std::string("Heard via relay\n");
        text += fmt("%u packets, last %s ago\n", n.packets, age_text(static_cast<long>(now - n.last_heard)).c_str());
        text += n.logged ? (n.is_new ? "New - logged to CSV" : "Logged to CSV")
                         : (n.meshtastic ? "" : "Not logged (needs GPS)");
        if (!n.detail.empty()) text += "  " + n.detail;
        lv_label_set_text(detail_, text.c_str());
    }
}

// ---------------------------------------------------------------------------
// Live
// ---------------------------------------------------------------------------

LiveScreen::LiveScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets)
    : MeshScreen(view_model, assets) {
    init();
}

void LiveScreen::build_page(lv_obj_t* content) {
    rows_.build(content, 2, kRowH, 7,
                {{4, 54}, {60, 12}, {74, 196}, {272, 44, LV_TEXT_ALIGN_RIGHT}}, font());
    empty_ = lv_label_create(content);
    lv_obj_set_width(empty_, 300);
    lv_obj_set_style_text_font(empty_, font(), 0);
    lv_obj_set_style_text_align(empty_, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(empty_, "No packets yet");
    lv_obj_align(empty_, LV_ALIGN_CENTER, 0, 0);
    refresh();
}

void LiveScreen::refresh() {
    auto* engine = view_model().engine();
    const auto colors = view::palette(view_model().is_dark_mode());
    rows_.apply_palette(colors);
    lv_obj_set_style_text_color(empty_, colors.text_disabled, 0);
    if (!engine) return;
    const auto feed = engine->feed();
    const int total = static_cast<int>(feed.size());
    set_visible(empty_, total == 0);
    const int cursor = view_model().cursor(model::AppPage::Live);
    const int start = window_start(cursor, rows_.rows(), total);
    for (int r = 0; r < rows_.rows(); ++r) {
        const int idx = start + r;
        if (idx >= total) {
            rows_.set_row_visible(r, false);
            continue;
        }
        rows_.set_row_visible(r, true);
        const auto& f = feed[static_cast<size_t>(idx)];
        rows_.set_text(r, 0, f.time);
        rows_.set_color(r, 0, colors.text_disabled);
        rows_.set_text(r, 1, std::string(1, f.proto));
        rows_.set_color(r, 1, f.proto == 'C'   ? colors.info
                              : f.proto == 'T' ? colors.warning
                              : f.proto == '!' ? colors.error
                                               : colors.text_disabled);
        rows_.set_text(r, 2, f.text);
        if (f.proto == '!') rows_.set_color(r, 2, colors.text_disabled);
        rows_.set_text(r, 3, fmt("%.0f", f.rssi));
        rows_.set_selected(r, cursor > 0 && idx == cursor, colors);
    }
}

// ---------------------------------------------------------------------------
// Radio
// ---------------------------------------------------------------------------

RadioScreen::RadioScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets)
    : MeshScreen(view_model, assets) {
    init();
}

void RadioScreen::build_page(lv_obj_t* content) {
    header_ = make_label(content, 6, 1, 220, LV_TEXT_ALIGN_LEFT);
    mode_ = make_label(content, 226, 1, 88, LV_TEXT_ALIGN_RIGHT);
    status_ = make_label(content, 6, 16, 308, LV_TEXT_ALIGN_LEFT);
    rows_.build(content, 33, kRowH, 5,
                {{4, 24}, {30, 150}, {182, 78, LV_TEXT_ALIGN_RIGHT}, {262, 52, LV_TEXT_ALIGN_RIGHT}}, font());
    refresh();
}

void RadioScreen::refresh() {
    auto* engine = view_model().engine();
    const auto colors = view::palette(view_model().is_dark_mode());
    rows_.apply_palette(colors);
    lv_obj_set_style_text_color(header_, colors.text, 0);
    lv_obj_set_style_text_color(mode_, colors.primary, 0);
    if (!engine) return;

    const auto status = engine->radio();
    const auto settings = engine->settings();
    const auto profiles = engine->region_profiles();
    const bool multi = engine->multi_radio();
    const auto selected = settings.selected_profiles();
    std::vector<std::string> on_cap, on_xiao;
    if (multi) {
        for (const auto& p : settings.radio_profiles(0)) on_cap.push_back(p.key);
        for (const auto& p : settings.radio_profiles(1)) on_xiao.push_back(p.key);
    }

    lv_label_set_text(header_, (status.source + "  " + radio_state_text(status.state)).c_str());
    lv_label_set_text(mode_, fmt("%s %s", multi ? "CAP+XIAO" : settings.hop ? "HOP" : "FIXED",
                                 mesh::region_name(settings.region))
                                 .c_str());
    if (status.state == mesh::RadioState::Error || status.state == mesh::RadioState::Starting) {
        lv_label_set_text(status_, status.message.c_str());
        lv_obj_set_style_text_color(status_, status.state == mesh::RadioState::Error ? colors.error : colors.warning,
                                    0);
    } else {
        const std::string now = status.rssi_now > -159 ? fmt("%.0f dBm", status.rssi_now) : std::string("--");
        lv_label_set_text(status_, fmt("Now %s   %llu hops%s", now.c_str(),
                                       static_cast<unsigned long long>(status.hops),
                                       status.locked ? "   receiving..." : "")
                                       .c_str());
        lv_obj_set_style_text_color(status_, colors.text_disabled, 0);
    }

    const int total = static_cast<int>(profiles.size());
    const int cursor = view_model().cursor(model::AppPage::Radio);
    const int start = window_start(cursor, rows_.rows(), total);
    const std::string current_key =
        status.current >= 0 && status.current < static_cast<int>(status.stats.size())
            ? status.stats[static_cast<size_t>(status.current)].key
            : std::string();
    for (int r = 0; r < rows_.rows(); ++r) {
        const int idx = start + r;
        if (idx >= total) {
            rows_.set_row_visible(r, false);
            continue;
        }
        rows_.set_row_visible(r, true);
        const auto& p = profiles[static_cast<size_t>(idx)];
        bool in_use = false;
        if (multi) {
            const bool cap = std::find(on_cap.begin(), on_cap.end(), p.key) != on_cap.end();
            const bool xiao = std::find(on_xiao.begin(), on_xiao.end(), p.key) != on_xiao.end();
            in_use = cap || xiao;
            rows_.set_text(r, 0, cap ? "[C]" : xiao ? "[X]" : "[ ]");
        } else {
            for (const auto& s : selected) in_use = in_use || s.key == p.key;
            rows_.set_text(r, 0, settings.hop ? (in_use ? "[x]" : "[ ]") : (in_use ? "(o)" : "( )"));
        }
        rows_.set_color(r, 0, in_use ? colors.primary : colors.text_disabled);
        rows_.set_text(r, 1, p.name);
        const bool on_air = p.key == current_key || std::find(status.current_keys.begin(), status.current_keys.end(),
                                                              p.key) != status.current_keys.end();
        if (on_air) rows_.set_color(r, 1, colors.primary);
        else if (!in_use) rows_.set_color(r, 1, colors.text_disabled);
        const mesh::ProfileStats* st = nullptr;
        for (const auto& s : status.stats) {
            if (s.key == p.key) st = &s;
        }
        if (st) {
            rows_.set_text(r, 2, fmt("%u ok %u bad", st->rx_ok, st->rx_bad));
            rows_.set_text(r, 3, st->peak_rssi > -159 ? fmt("%.0f", st->peak_rssi) : "");
        } else {
            rows_.set_text(r, 2, p.short_label());
            rows_.set_color(r, 2, colors.text_disabled);
            rows_.set_text(r, 3, "");
        }
        rows_.set_selected(r, idx == cursor, colors);
    }
}

// ---------------------------------------------------------------------------
// GPS & logs
// ---------------------------------------------------------------------------

GpsScreen::GpsScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets)
    : MeshScreen(view_model, assets) {
    init();
}

void GpsScreen::build_page(lv_obj_t* content) {
    rows_.build(content, 2, kRowH, 7, {{6, 56}, {64, 250}}, font());
    refresh();
}

void GpsScreen::refresh() {
    auto* engine = view_model().engine();
    const auto colors = view::palette(view_model().is_dark_mode());
    rows_.apply_palette(colors);
    for (int r = 0; r < rows_.rows(); ++r) rows_.set_color(r, 0, colors.text_disabled);
    if (!engine) return;
    const auto g = engine->gps();
    const auto c = engine->counters();

    rows_.set_text(0, 0, "Fix");
    if (!g.enabled) {
        rows_.set_text(0, 1, "GPS disabled in settings");
    } else if (!g.port_open) {
        rows_.set_text(0, 1, "Port closed: " + g.message);
        rows_.set_color(0, 1, colors.error);
    } else if (!g.receiving) {
        rows_.set_text(0, 1, "No data from GPS");
        rows_.set_color(0, 1, colors.warning);
    } else if (g.has_fix) {
        rows_.set_text(0, 1, fmt("Fix, %d sats, HDOP %.1f", g.satellites, g.hdop));
        rows_.set_color(0, 1, colors.success);
    } else {
        rows_.set_text(0, 1, fmt("Searching, %d sats", g.satellites));
        rows_.set_color(0, 1, colors.warning);
    }
    rows_.set_text(1, 0, "Position");
    rows_.set_text(1, 1, g.has_fix ? fmt("%.6f, %.6f", g.lat, g.lon) : std::string("--"));
    rows_.set_text(2, 0, "Alt/Spd");
    rows_.set_text(2, 1, g.has_fix ? fmt("%.0f m   %.0f km/h", g.alt_m, g.speed_kmh) : std::string("--"));
    rows_.set_text(3, 0, "UTC");
    rows_.set_text(3, 1, (g.utc_date + " " + g.utc_time).c_str());
    rows_.set_text(4, 0, "Port");
    rows_.set_text(4, 1, g.device + (g.message.empty() ? "" : "  " + g.message));
    rows_.set_text(5, 0, "Packets");
    rows_.set_text(5, 1,
                   fmt("%llu rx  %llu bad  %llu adverts  %llu bad sig",
                       static_cast<unsigned long long>(c.packets), static_cast<unsigned long long>(c.bad_crc),
                       static_cast<unsigned long long>(c.adverts),
                       static_cast<unsigned long long>(c.bad_signatures)));
    rows_.set_text(6, 0, "Logs");
    if (!c.storage_error.empty()) {
        rows_.set_text(6, 1, c.storage_error);
        rows_.set_color(6, 1, colors.error);
    } else {
        rows_.set_text(6, 1, c.data_dir);
    }
}

}  // namespace screen
