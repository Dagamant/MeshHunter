// A fixed grid of label rows for the 320x110 content area: the MeshHunter
// screens are all "a few lines of live text", so each screen builds one of
// these once and just rewrites the label texts when data changes (cheap,
// and no LVGL object churn on every packet).

#pragma once

#include "theme.h"

#include "lvgl.h"

#include <string>
#include <vector>

namespace screen {

struct Column {
    int32_t x;
    int32_t width;
    lv_text_align_t align = LV_TEXT_ALIGN_LEFT;
};

class TextRows {
public:
    void build(lv_obj_t* parent, int32_t y, int32_t row_height, int rows, std::vector<Column> columns,
               const lv_font_t* font);

    int rows() const { return static_cast<int>(row_objs_.size()); }
    void set_text(int row, int col, const std::string& text);
    void set_color(int row, int col, lv_color_t color);
    // Highlights a row with the theme's primary colour at low opacity.
    void set_selected(int row, bool selected, const view::ThemePalette& colors);
    void set_row_visible(int row, bool visible);
    // Default text colour for every cell.
    void apply_palette(const view::ThemePalette& colors);

private:
    std::vector<lv_obj_t*> row_objs_;
    std::vector<std::vector<lv_obj_t*>> labels_;
};

// "5s", "3m", "2h", "4d"
std::string age_text(long seconds);
// Window start so `cursor` stays visible in `visible` rows of `total`.
int window_start(int cursor, int visible, int total);

}  // namespace screen
