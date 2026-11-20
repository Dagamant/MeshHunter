#include "csv_table.h"

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>

namespace mesh {

std::vector<std::vector<std::string>> parse_csv(const std::string& text) {
    std::vector<std::vector<std::string>> rows;
    std::vector<std::string> row;
    std::string field;
    bool in_quotes = false;
    bool field_started = false;
    size_t i = 0;
    // Skip a UTF-8 BOM.
    if (text.size() >= 3 && text.compare(0, 3, "\xEF\xBB\xBF") == 0) i = 3;
    for (; i < text.size(); ++i) {
        const char c = text[i];
        if (in_quotes) {
            if (c == '"') {
                if (i + 1 < text.size() && text[i + 1] == '"') {
                    field.push_back('"');
                    ++i;
                } else {
                    in_quotes = false;
                }
            } else {
                field.push_back(c);
            }
            continue;
        }
        if (c == '"' && !field_started) {
            in_quotes = true;
            field_started = true;
        } else if (c == ',') {
            row.push_back(std::move(field));
            field.clear();
            field_started = false;
        } else if (c == '\r' || c == '\n') {
            if (c == '\r' && i + 1 < text.size() && text[i + 1] == '\n') ++i;
            row.push_back(std::move(field));
            field.clear();
            field_started = false;
            if (!(row.size() == 1 && row[0].empty())) rows.push_back(std::move(row));
            row.clear();
        } else {
            field.push_back(c);
            field_started = true;
        }
    }
    if (field_started || !field.empty() || !row.empty()) {
        row.push_back(std::move(field));
        rows.push_back(std::move(row));
    }
    return rows;
}

std::string csv_field(const std::string& value) {
    const bool needs_quotes = value.find_first_of(",\"\r\n") != std::string::npos;
    if (!needs_quotes) return value;
    std::string out = "\"";
    for (char c : value) {
        if (c == '"') out.push_back('"');
        out.push_back(c);
    }
    out.push_back('"');
    return out;
}

CsvTable::CsvTable(std::string path, std::vector<std::string> fields, std::string key_field, std::string sort_field)
    : path_(std::move(path)),
      fields_(std::move(fields)),
      key_field_(std::move(key_field)),
      sort_field_(std::move(sort_field)) {}

bool CsvTable::load(std::string* error) {
    rows_.clear();
    dirty_ = false;
    std::ifstream in(path_, std::ios::binary);
    if (!in) return true;  // no file yet
    std::stringstream ss;
    ss << in.rdbuf();
    const auto parsed = parse_csv(ss.str());
    if (parsed.empty()) return true;
    const auto& header = parsed.front();
    for (size_t r = 1; r < parsed.size(); ++r) {
        CsvRow row;
        for (size_t c = 0; c < header.size() && c < parsed[r].size(); ++c) {
            if (std::find(fields_.begin(), fields_.end(), header[c]) != fields_.end()) {
                row[header[c]] = parsed[r][c];
            }
        }
        const auto it = row.find(key_field_);
        if (it == row.end() || it->second.empty()) continue;
        rows_[it->second] = std::move(row);
    }
    if (error) error->clear();
    return true;
}

bool CsvTable::save(std::string* error) {
    std::vector<const CsvRow*> ordered;
    ordered.reserve(rows_.size());
    for (const auto& kv : rows_) ordered.push_back(&kv.second);
    const std::string& sort = sort_field_;
    std::stable_sort(ordered.begin(), ordered.end(), [&sort](const CsvRow* a, const CsvRow* b) {
        const auto ia = a->find(sort);
        const auto ib = b->find(sort);
        const std::string& va = ia == a->end() ? std::string() : ia->second;
        const std::string& vb = ib == b->end() ? std::string() : ib->second;
        return va < vb;
    });

    std::string out;
    for (size_t i = 0; i < fields_.size(); ++i) {
        if (i) out.push_back(',');
        out += csv_field(fields_[i]);
    }
    out += "\r\n";
    for (const CsvRow* row : ordered) {
        for (size_t i = 0; i < fields_.size(); ++i) {
            if (i) out.push_back(',');
            const auto it = row->find(fields_[i]);
            if (it != row->end()) out += csv_field(it->second);
        }
        out += "\r\n";
    }

    const std::string tmp = path_ + ".tmp";
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) {
            if (error) *error = "can't write " + tmp + ": " + std::strerror(errno);
            return false;
        }
        f << out;
        f.flush();
        if (!f) {
            if (error) *error = "write failed for " + tmp;
            return false;
        }
    }
    if (std::rename(tmp.c_str(), path_.c_str()) != 0) {
        if (error) *error = "can't replace " + path_ + ": " + std::strerror(errno);
        return false;
    }
    dirty_ = false;
    return true;
}

CsvRow* CsvTable::find(const std::string& key) {
    const auto it = rows_.find(key);
    return it == rows_.end() ? nullptr : &it->second;
}

CsvRow& CsvTable::upsert(const std::string& key, bool* created) {
    auto [it, inserted] = rows_.try_emplace(key);
    if (inserted) it->second[key_field_] = key;
    if (created) *created = inserted;
    dirty_ = true;
    return it->second;
}

}  // namespace mesh
