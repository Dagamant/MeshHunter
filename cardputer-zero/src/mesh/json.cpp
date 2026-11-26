#include "json.h"

#include "bytes.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace mesh::json {

std::string escape(const std::string& text) {
    std::string out;
    out.reserve(text.size() + 2);
    out.push_back('"');
    for (unsigned char c : text) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    out.push_back('"');
    return out;
}

void ObjectWriter::key(const char* k) {
    if (!first_) out_.push_back(',');
    first_ = false;
    out_ += escape(k);
    out_.push_back(':');
}

ObjectWriter& ObjectWriter::str(const char* k, const std::string& value) {
    key(k);
    out_ += escape(value);
    return *this;
}

ObjectWriter& ObjectWriter::num(const char* k, double value, int decimals) {
    key(k);
    if (decimals >= 0) {
        out_ += format_float(value, decimals);
    } else {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "%.10g", value);
        out_ += buf;
    }
    return *this;
}

ObjectWriter& ObjectWriter::integer(const char* k, long long value) {
    key(k);
    out_ += std::to_string(value);
    return *this;
}

ObjectWriter& ObjectWriter::boolean(const char* k, bool value) {
    key(k);
    out_ += value ? "true" : "false";
    return *this;
}

ObjectWriter& ObjectWriter::raw(const char* k, const std::string& json) {
    key(k);
    out_ += json;
    return *this;
}

namespace {

// Position just after `"key":` (and any spaces), or npos.
size_t value_pos(const std::string& line, const char* key) {
    const std::string needle = std::string("\"") + key + "\"";
    size_t pos = 0;
    while ((pos = line.find(needle, pos)) != std::string::npos) {
        size_t p = pos + needle.size();
        while (p < line.size() && line[p] == ' ') ++p;
        if (p < line.size() && line[p] == ':') {
            ++p;
            while (p < line.size() && line[p] == ' ') ++p;
            return p;
        }
        pos = p;
    }
    return std::string::npos;
}

}  // namespace

bool get_string(const std::string& line, const char* key, std::string& out) {
    size_t p = value_pos(line, key);
    if (p == std::string::npos || p >= line.size() || line[p] != '"') return false;
    out.clear();
    for (++p; p < line.size(); ++p) {
        const char c = line[p];
        if (c == '"') return true;
        if (c == '\\' && p + 1 < line.size()) {
            const char e = line[++p];
            switch (e) {
                case 'n': out.push_back('\n'); break;
                case 't': out.push_back('\t'); break;
                case 'r': out.push_back('\r'); break;
                case 'u':
                    // Good enough for replay: non-ASCII escapes become '?'.
                    p += 4;
                    out.push_back('?');
                    break;
                default: out.push_back(e);
            }
        } else {
            out.push_back(c);
        }
    }
    return false;
}

bool get_number(const std::string& line, const char* key, double& out) {
    const size_t p = value_pos(line, key);
    if (p == std::string::npos) return false;
    const char* start = line.c_str() + p;
    char* end = nullptr;
    const double v = std::strtod(start, &end);
    if (end == start) return false;
    out = v;
    return true;
}

bool get_bool(const std::string& line, const char* key, bool& out) {
    const size_t p = value_pos(line, key);
    if (p == std::string::npos) return false;
    if (line.compare(p, 4, "true") == 0) {
        out = true;
        return true;
    }
    if (line.compare(p, 5, "false") == 0) {
        out = false;
        return true;
    }
    return false;
}

bool get_number_rows(const std::string& line, const char* key, std::vector<std::vector<double>>& out) {
    size_t p = value_pos(line, key);
    if (p == std::string::npos || p >= line.size() || line[p] != '[') return false;
    out.clear();
    bool in_row = false;
    for (++p; p < line.size(); ++p) {
        const char c = line[p];
        if (c == ' ' || c == ',') continue;
        if (c == '[' && !in_row) {
            in_row = true;
            out.emplace_back();
        } else if (c == ']') {
            if (!in_row) return true;
            in_row = false;
        } else if (in_row) {
            const char* start = line.c_str() + p;
            char* end = nullptr;
            const double v = std::strtod(start, &end);
            if (end == start) return false;
            out.back().push_back(v);
            p += static_cast<size_t>(end - start) - 1;
        } else {
            return false;
        }
    }
    return false;
}

}  // namespace mesh::json
