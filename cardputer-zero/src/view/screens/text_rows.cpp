#include "text_rows.h"

#include <algorithm>

namespace screen {

void TextRows::build(lv_obj_t* parent, int32_t y, int32_t row_height, int rows, std::vector<Column> columns,
                     const lv_font_t* font) {
    row_objs_.clear();
    labels_.clear();
    for (int r = 0; r < rows; ++r) {
        auto* row = lv_obj_create(parent);
        lv_obj_remove_style_all(row);
        lv_obj_set_size(row, LV_PCT(100), row_height);
        lv_obj_set_pos(row, 0, y + r * row_height);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_clear_flag(row, LV_OBJ_FLAG_CLICKABLE);
        row_objs_.push_back(row);

        std::vector<lv_obj_t*> cells;
        for (const auto& col : columns) {
            auto* label = lv_label_create(row);
            lv_label_set_text(label, "");
            lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
            lv_obj_set_width(label, col.width);
            lv_obj_set_style_text_font(label, font, 0);
            lv_obj_set_style_text_align(label, col.align, 0);
            lv_obj_align(label, LV_ALIGN_LEFT_MID, col.x, 0);
            cells.push_back(label);
        }
        labels_.push_back(std::move(cells));
    }
}

void TextRows::set_text(int row, int col, const std::string& text) {
    if (row < 0 || row >= rows() || col < 0 || col >= static_cast<int>(labels_[static_cast<size_t>(row)].size())) {
        return;
    }
    auto* label = labels_[static_cast<size_t>(row)][static_cast<size_t>(col)];
    // Skip identical text: avoids needless redraws on every tick.
    const char* current = lv_label_get_text(label);
    if (current && text == current) return;
    lv_label_set_text(label, text.c_str());
}

void TextRows::set_color(int row, int col, lv_color_t color) {
    if (row < 0 || row >= rows() || col < 0 || col >= static_cast<int>(labels_[static_cast<size_t>(row)].size())) {
        return;
    }
    lv_obj_set_style_text_color(labels_[static_cast<size_t>(row)][static_cast<size_t>(col)], color, 0);
}

void TextRows::set_selected(int row, bool selected, const view::ThemePalette& colors) {
    if (row < 0 || row >= rows()) return;
    auto* obj = row_objs_[static_cast<size_t>(row)];
    lv_obj_set_style_bg_color(obj, colors.primary, 0);
    lv_obj_set_style_bg_opa(obj, selected ? LV_OPA_30 : LV_OPA_TRANSP, 0);
}

void TextRows::set_row_visible(int row, bool visible) {
    if (row < 0 || row >= rows()) return;
    auto* obj = row_objs_[static_cast<size_t>(row)];
    if (visible) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
}

void TextRows::apply_palette(const view::ThemePalette& colors) {
    for (auto& row : labels_) {
        for (auto* label : row) lv_obj_set_style_text_color(label, colors.text, 0);
    }
}

std::string age_text(long seconds) {
    if (seconds < 0) seconds = 0;
    if (seconds < 60) return std::to_string(seconds) + "s";
    if (seconds < 3600) return std::to_string(seconds / 60) + "m";
    if (seconds < 86400) return std::to_string(seconds / 3600) + "h";
    return std::to_string(seconds / 86400) + "d";
}

int window_start(int cursor, int visible, int total) {
    if (total <= visible) return 0;
    const int start = cursor - visible / 2;
    return std::clamp(start, 0, total - visible);
}

}  // namespace screen
