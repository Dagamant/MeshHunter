// A keyed CSV table with a fixed column list, read/written the way Python's
// csv.DictReader/DictWriter do (RFC 4180 quoting, \r\n line endings), so the
// files stay interchangeable with the desktop MeshHunter app.

#pragma once

#include <map>
#include <string>
#include <vector>

namespace mesh {

using CsvRow = std::map<std::string, std::string>;

std::vector<std::vector<std::string>> parse_csv(const std::string& text);
std::string csv_field(const std::string& value);

class CsvTable {
public:
    CsvTable(std::string path, std::vector<std::string> fields, std::string key_field, std::string sort_field);

    // Missing file = empty table. Columns not in `fields` are dropped, like
    // the desktop app does.
    bool load(std::string* error = nullptr);
    // Writes to a temp file and renames over the original.
    bool save(std::string* error = nullptr);

    bool contains(const std::string& key) const { return rows_.count(key) != 0; }
    CsvRow* find(const std::string& key);
    CsvRow& upsert(const std::string& key, bool* created = nullptr);
    const std::map<std::string, CsvRow>& rows() const { return rows_; }
    size_t size() const { return rows_.size(); }

    void mark_dirty() { dirty_ = true; }
    bool dirty() const { return dirty_; }
    const std::string& path() const { return path_; }

private:
    std::string path_;
    std::vector<std::string> fields_;
    std::string key_field_;
    std::string sort_field_;
    std::map<std::string, CsvRow> rows_;
    bool dirty_ = false;
};

}  // namespace mesh
