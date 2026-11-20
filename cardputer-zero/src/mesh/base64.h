#pragma once

#include "bytes.h"

#include <string>

namespace mesh {

// Standard base64 (with or without padding). Returns false on bad input.
bool base64_decode(const std::string& text, Bytes& out);

}  // namespace mesh
