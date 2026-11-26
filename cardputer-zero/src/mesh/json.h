// Just enough JSON for sniffer_packets.jsonl: a compact object writer, and
// flat field lookups for replaying lines the desktop app (or we) wrote.

#pragma once

#include <string>
#include <vector>

namespace mesh::json {

std::string escape(const std::string& text);

class ObjectWriter {
public:
    ObjectWriter& str(const char* key, const std::string& value);
    ObjectWriter& num(const char* key, double value, int decimals = -1);
    ObjectWriter& integer(const char* key, long long value);
    ObjectWriter& boolean(const char* key, bool value);
    ObjectWriter& raw(const char* key, const std::string& json);  // nested object/array
    std::string done() const { return out_ + "}"; }

private:
    void key(const char* k);
    std::string out_ = "{";
    bool first_ = true;
};

// First occurrence of "key": in the text (so top-level fields must come
// before any nested object that repeats the key -- true for our records).
bool get_string(const std::string& line, const char* key, std::string& out);
bool get_number(const std::string& line, const char* key, double& out);
bool get_bool(const std::string& line, const char* key, bool& out);
// An array of number arrays, e.g. "profiles":[[0,1,2],[1,3,4]]. Returns
// false if the key is missing or isn't an array.
bool get_number_rows(const std::string& line, const char* key, std::vector<std::vector<double>>& out);

}  // namespace mesh::json
