// MeshHunter's four pages. Each builds a fixed TextRows grid once and
// refreshes its texts when the view model publishes a new data revision,
// cursor move, or theme change.

#pragma once

#include "base_screen.h"
#include "text_rows.h"

namespace screen {

class MeshScreen : public BaseScreen {
public:
    MeshScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets);

protected:
    void build_content(lv_obj_t* content) final;
    virtual void build_page(lv_obj_t* content) = 0;
    virtual void refresh() = 0;

    const lv_font_t* font() const { return font_; }
    lv_obj_t* make_label(lv_obj_t* parent, int32_t x, int32_t y, int32_t w, lv_text_align_t align);

private:
    static void refresh_cb(lv_observer_t* observer, lv_subject_t* subject);
    const lv_font_t* font_ = nullptr;
};

class NodesScreen final : public MeshScreen {
public:
    NodesScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets);

private:
    void build_page(lv_obj_t* content) override;
    void refresh() override;

    lv_obj_t* header_ = nullptr;
    lv_obj_t* empty_ = nullptr;
    lv_obj_t* detail_ = nullptr;
    TextRows rows_;
};

class LiveScreen final : public MeshScreen {
public:
    LiveScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets);

private:
    void build_page(lv_obj_t* content) override;
    void refresh() override;

    lv_obj_t* empty_ = nullptr;
    TextRows rows_;
};

class RadioScreen final : public MeshScreen {
public:
    RadioScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets);

private:
    void build_page(lv_obj_t* content) override;
    void refresh() override;

    lv_obj_t* header_ = nullptr;
    lv_obj_t* mode_ = nullptr;
    lv_obj_t* status_ = nullptr;
    TextRows rows_;
};

class GpsScreen final : public MeshScreen {
public:
    GpsScreen(viewmodel::BaseViewModel& view_model, app::AssetManager& assets);

private:
    void build_page(lv_obj_t* content) override;
    void refresh() override;

    TextRows rows_;
};

}  // namespace screen
