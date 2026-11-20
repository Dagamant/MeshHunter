/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

#pragma once

namespace model {

enum class AppPage {
    Nodes = 0,
    Live = 1,
    Radio = 2,
    Gps = 3,
};

constexpr int kPageCount = 4;

class BaseModel {
public:
    const char* app_title() const;

    bool dark_mode() const;
    void set_dark_mode(bool enabled);
    void toggle_dark_mode();

    AppPage current_page() const;
    void set_current_page(AppPage page);
    void step_page(int delta);

private:
    bool dark_mode_ = true;
    AppPage current_page_ = AppPage::Nodes;
};

const char* page_title(AppPage page);

} // namespace model
