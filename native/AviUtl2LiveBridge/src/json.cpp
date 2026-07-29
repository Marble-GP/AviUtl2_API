#include "json.hpp"

#include "bridge_constants.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <limits>
#include <system_error>
#include <type_traits>

namespace aviutl2::live {
namespace {

[[nodiscard]] int hex_value(const char character) noexcept {
    if (character >= '0' && character <= '9') {
        return character - '0';
    }
    if (character >= 'a' && character <= 'f') {
        return character - 'a' + 10;
    }
    if (character >= 'A' && character <= 'F') {
        return character - 'A' + 10;
    }
    return -1;
}

void append_code_point(std::string& output, const std::uint32_t code_point) {
    if (code_point <= 0x7FU) {
        output.push_back(static_cast<char>(code_point));
    } else if (code_point <= 0x7FFU) {
        output.push_back(static_cast<char>(0xC0U | (code_point >> 6U)));
        output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
    } else if (code_point <= 0xFFFFU) {
        output.push_back(static_cast<char>(0xE0U | (code_point >> 12U)));
        output.push_back(static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
        output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
    } else {
        output.push_back(static_cast<char>(0xF0U | (code_point >> 18U)));
        output.push_back(static_cast<char>(0x80U | ((code_point >> 12U) & 0x3FU)));
        output.push_back(static_cast<char>(0x80U | ((code_point >> 6U) & 0x3FU)));
        output.push_back(static_cast<char>(0x80U | (code_point & 0x3FU)));
    }
}

class Parser final {
public:
    explicit Parser(const std::string_view input) noexcept : input_(input) {}

    [[nodiscard]] Json parse() {
        skip_whitespace();
        Json result = parse_value(0U);
        skip_whitespace();
        if (position_ != input_.size()) {
            fail("unexpected trailing data");
        }
        return result;
    }

private:
    [[noreturn]] void fail(const std::string& message) const {
        throw JsonParseError(message, position_);
    }

    void skip_whitespace() noexcept {
        while (position_ < input_.size()) {
            const char character = input_[position_];
            if (character != ' ' && character != '\t' && character != '\r' &&
                character != '\n') {
                return;
            }
            ++position_;
        }
    }

    bool consume(const char expected) noexcept {
        if (position_ < input_.size() && input_[position_] == expected) {
            ++position_;
            return true;
        }
        return false;
    }

    void require(const char expected) {
        if (!consume(expected)) {
            fail(std::string("expected '") + expected + "'");
        }
    }

    [[nodiscard]] Json parse_value(const std::size_t depth) {
        if (depth > kMaxJsonDepth) {
            fail("maximum JSON nesting depth exceeded");
        }
        skip_whitespace();
        if (position_ >= input_.size()) {
            fail("unexpected end of input");
        }

        switch (input_[position_]) {
            case 'n':
                parse_literal("null");
                return Json(nullptr);
            case 't':
                parse_literal("true");
                return Json(true);
            case 'f':
                parse_literal("false");
                return Json(false);
            case '"':
                return Json(parse_string());
            case '[':
                return Json(parse_array(depth + 1U));
            case '{':
                return Json(parse_object(depth + 1U));
            default:
                if (input_[position_] == '-' ||
                    (input_[position_] >= '0' && input_[position_] <= '9')) {
                    return parse_number();
                }
                fail("unexpected token");
        }
    }

    void parse_literal(const std::string_view literal) {
        if (input_.substr(position_, literal.size()) != literal) {
            fail("invalid literal");
        }
        position_ += literal.size();
    }

    [[nodiscard]] std::uint16_t parse_hex_quad() {
        if (input_.size() - position_ < 4U) {
            fail("incomplete unicode escape");
        }
        std::uint16_t result = 0;
        for (int index = 0; index < 4; ++index) {
            const int value = hex_value(input_[position_++]);
            if (value < 0) {
                fail("invalid unicode escape");
            }
            result = static_cast<std::uint16_t>((result << 4U) |
                                                static_cast<unsigned int>(value));
        }
        return result;
    }

    [[nodiscard]] std::string parse_string() {
        require('"');
        std::string result;
        while (position_ < input_.size()) {
            const unsigned char character =
                static_cast<unsigned char>(input_[position_++]);
            if (character == '"') {
                return result;
            }
            if (character < 0x20U) {
                fail("unescaped control character in string");
            }
            if (character != '\\') {
                result.push_back(static_cast<char>(character));
                continue;
            }
            if (position_ >= input_.size()) {
                fail("incomplete string escape");
            }
            const char escape = input_[position_++];
            switch (escape) {
                case '"':
                case '\\':
                case '/':
                    result.push_back(escape);
                    break;
                case 'b':
                    result.push_back('\b');
                    break;
                case 'f':
                    result.push_back('\f');
                    break;
                case 'n':
                    result.push_back('\n');
                    break;
                case 'r':
                    result.push_back('\r');
                    break;
                case 't':
                    result.push_back('\t');
                    break;
                case 'u': {
                    const std::uint16_t first = parse_hex_quad();
                    std::uint32_t code_point = first;
                    if (first >= 0xD800U && first <= 0xDBFFU) {
                        if (!consume('\\') || !consume('u')) {
                            fail("missing low surrogate");
                        }
                        const std::uint16_t second = parse_hex_quad();
                        if (second < 0xDC00U || second > 0xDFFFU) {
                            fail("invalid low surrogate");
                        }
                        code_point =
                            0x10000U +
                            ((static_cast<std::uint32_t>(first) - 0xD800U) << 10U) +
                            (static_cast<std::uint32_t>(second) - 0xDC00U);
                    } else if (first >= 0xDC00U && first <= 0xDFFFU) {
                        fail("unexpected low surrogate");
                    }
                    append_code_point(result, code_point);
                    break;
                }
                default:
                    fail("invalid string escape");
            }
        }
        fail("unterminated string");
    }

    [[nodiscard]] Json::Array parse_array(const std::size_t depth) {
        require('[');
        Json::Array result;
        skip_whitespace();
        if (consume(']')) {
            return result;
        }
        while (true) {
            result.push_back(parse_value(depth));
            skip_whitespace();
            if (consume(']')) {
                return result;
            }
            require(',');
            skip_whitespace();
        }
    }

    [[nodiscard]] Json::Object parse_object(const std::size_t depth) {
        require('{');
        Json::Object result;
        skip_whitespace();
        if (consume('}')) {
            return result;
        }
        while (true) {
            if (position_ >= input_.size() || input_[position_] != '"') {
                fail("object key must be a string");
            }
            std::string key = parse_string();
            skip_whitespace();
            require(':');
            Json value = parse_value(depth);
            const auto [iterator, inserted] =
                result.emplace(std::move(key), std::move(value));
            static_cast<void>(iterator);
            if (!inserted) {
                fail("duplicate object key");
            }
            skip_whitespace();
            if (consume('}')) {
                return result;
            }
            require(',');
            skip_whitespace();
        }
    }

    [[nodiscard]] Json parse_number() {
        const std::size_t start = position_;
        consume('-');
        if (position_ >= input_.size()) {
            fail("incomplete number");
        }
        if (consume('0')) {
            if (position_ < input_.size() && input_[position_] >= '0' &&
                input_[position_] <= '9') {
                fail("leading zero in number");
            }
        } else {
            if (input_[position_] < '1' || input_[position_] > '9') {
                fail("invalid number");
            }
            while (position_ < input_.size() && input_[position_] >= '0' &&
                   input_[position_] <= '9') {
                ++position_;
            }
        }

        bool integer = true;
        if (consume('.')) {
            integer = false;
            const std::size_t fraction_start = position_;
            while (position_ < input_.size() && input_[position_] >= '0' &&
                   input_[position_] <= '9') {
                ++position_;
            }
            if (position_ == fraction_start) {
                fail("missing fractional digits");
            }
        }
        if (position_ < input_.size() &&
            (input_[position_] == 'e' || input_[position_] == 'E')) {
            integer = false;
            ++position_;
            if (position_ < input_.size() &&
                (input_[position_] == '+' || input_[position_] == '-')) {
                ++position_;
            }
            const std::size_t exponent_start = position_;
            while (position_ < input_.size() && input_[position_] >= '0' &&
                   input_[position_] <= '9') {
                ++position_;
            }
            if (position_ == exponent_start) {
                fail("missing exponent digits");
            }
        }

        const std::string_view text = input_.substr(start, position_ - start);
        if (integer) {
            std::int64_t value = 0;
            const auto parsed =
                std::from_chars(text.data(), text.data() + text.size(), value);
            if (parsed.ec == std::errc{} && parsed.ptr == text.data() + text.size()) {
                return Json(value);
            }
        }

        double value = 0.0;
        const auto parsed =
            std::from_chars(text.data(), text.data() + text.size(), value);
        if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size() ||
            !std::isfinite(value)) {
            fail("number is out of range");
        }
        return Json(value);
    }

    std::string_view input_;
    std::size_t position_ = 0;
};

void serialize_string(const std::string_view value, std::string& output) {
    output.push_back('"');
    constexpr char hex[] = "0123456789abcdef";
    for (const unsigned char character : value) {
        switch (character) {
            case '"':
                output.append("\\\"");
                break;
            case '\\':
                output.append("\\\\");
                break;
            case '\b':
                output.append("\\b");
                break;
            case '\f':
                output.append("\\f");
                break;
            case '\n':
                output.append("\\n");
                break;
            case '\r':
                output.append("\\r");
                break;
            case '\t':
                output.append("\\t");
                break;
            default:
                if (character < 0x20U) {
                    output.append("\\u00");
                    output.push_back(hex[(character >> 4U) & 0xFU]);
                    output.push_back(hex[character & 0xFU]);
                } else {
                    output.push_back(static_cast<char>(character));
                }
                break;
        }
    }
    output.push_back('"');
}

void serialize_value(const Json& value, std::string& output) {
    std::visit(
        [&output](const auto& item) {
            using Item = std::decay_t<decltype(item)>;
            if constexpr (std::is_same_v<Item, std::nullptr_t>) {
                output.append("null");
            } else if constexpr (std::is_same_v<Item, bool>) {
                output.append(item ? "true" : "false");
            } else if constexpr (std::is_same_v<Item, std::int64_t>) {
                char buffer[32]{};
                const auto result = std::to_chars(std::begin(buffer), std::end(buffer), item);
                output.append(buffer, result.ptr);
            } else if constexpr (std::is_same_v<Item, double>) {
                char buffer[64]{};
                const auto result = std::to_chars(
                    std::begin(buffer),
                    std::end(buffer),
                    item,
                    std::chars_format::general,
                    std::numeric_limits<double>::max_digits10);
                if (result.ec != std::errc{}) {
                    throw std::runtime_error("failed to serialize number");
                }
                output.append(buffer, result.ptr);
            } else if constexpr (std::is_same_v<Item, std::string>) {
                serialize_string(item, output);
            } else if constexpr (std::is_same_v<Item, Json::Array>) {
                output.push_back('[');
                bool first = true;
                for (const Json& child : item) {
                    if (!first) {
                        output.push_back(',');
                    }
                    first = false;
                    serialize_value(child, output);
                }
                output.push_back(']');
            } else if constexpr (std::is_same_v<Item, Json::Object>) {
                output.push_back('{');
                bool first = true;
                for (const auto& [key, child] : item) {
                    if (!first) {
                        output.push_back(',');
                    }
                    first = false;
                    serialize_string(key, output);
                    output.push_back(':');
                    serialize_value(child, output);
                }
                output.push_back('}');
            }
        },
        value.value());
}

}  // namespace

Json::Json() noexcept : value_(nullptr) {}
Json::Json(std::nullptr_t) noexcept : value_(nullptr) {}
Json::Json(const bool value) noexcept : value_(value) {}
Json::Json(const int value) noexcept : value_(static_cast<std::int64_t>(value)) {}
Json::Json(const std::int64_t value) noexcept : value_(value) {}
Json::Json(const double value) noexcept : value_(value) {}
Json::Json(const char* value) : value_(std::string(value)) {}
Json::Json(std::string value) : value_(std::move(value)) {}
Json::Json(Array value) : value_(std::move(value)) {}
Json::Json(Object value) : value_(std::move(value)) {}

bool Json::is_null() const noexcept {
    return std::holds_alternative<std::nullptr_t>(value_);
}
bool Json::is_bool() const noexcept {
    return std::holds_alternative<bool>(value_);
}
bool Json::is_integer() const noexcept {
    return std::holds_alternative<std::int64_t>(value_);
}
bool Json::is_number() const noexcept {
    return is_integer() || std::holds_alternative<double>(value_);
}
bool Json::is_string() const noexcept {
    return std::holds_alternative<std::string>(value_);
}
bool Json::is_array() const noexcept {
    return std::holds_alternative<Array>(value_);
}
bool Json::is_object() const noexcept {
    return std::holds_alternative<Object>(value_);
}
bool Json::as_bool() const {
    return std::get<bool>(value_);
}
std::int64_t Json::as_integer() const {
    return std::get<std::int64_t>(value_);
}
double Json::as_number() const {
    if (is_integer()) {
        return static_cast<double>(as_integer());
    }
    return std::get<double>(value_);
}
const std::string& Json::as_string() const {
    return std::get<std::string>(value_);
}
const Json::Array& Json::as_array() const {
    return std::get<Array>(value_);
}
const Json::Object& Json::as_object() const {
    return std::get<Object>(value_);
}
Json::Array& Json::as_array() {
    return std::get<Array>(value_);
}
Json::Object& Json::as_object() {
    return std::get<Object>(value_);
}
const Json* Json::find(const std::string_view key) const noexcept {
    if (!is_object()) {
        return nullptr;
    }
    const Object& object = std::get<Object>(value_);
    const auto iterator = object.find(key);
    return iterator == object.end() ? nullptr : &iterator->second;
}
const Json::Value& Json::value() const noexcept {
    return value_;
}

JsonParseError::JsonParseError(std::string message, const std::size_t offset)
    : std::runtime_error(std::move(message)), offset_(offset) {}
std::size_t JsonParseError::offset() const noexcept {
    return offset_;
}

Json parse_json(const std::string_view input) {
    if (!is_valid_utf8(input)) {
        throw JsonParseError("payload is not valid UTF-8", 0U);
    }
    return Parser(input).parse();
}

std::string serialize_json(const Json& value) {
    std::string output;
    output.reserve(256U);
    serialize_value(value, output);
    return output;
}

bool is_valid_utf8(const std::string_view input) noexcept {
    std::size_t index = 0;
    while (index < input.size()) {
        const auto first = static_cast<unsigned char>(input[index]);
        if (first <= 0x7FU) {
            ++index;
            continue;
        }

        std::size_t count = 0;
        std::uint32_t code_point = 0;
        if (first >= 0xC2U && first <= 0xDFU) {
            count = 2U;
            code_point = first & 0x1FU;
        } else if (first >= 0xE0U && first <= 0xEFU) {
            count = 3U;
            code_point = first & 0x0FU;
        } else if (first >= 0xF0U && first <= 0xF4U) {
            count = 4U;
            code_point = first & 0x07U;
        } else {
            return false;
        }
        if (input.size() - index < count) {
            return false;
        }
        for (std::size_t offset = 1U; offset < count; ++offset) {
            const auto continuation =
                static_cast<unsigned char>(input[index + offset]);
            if ((continuation & 0xC0U) != 0x80U) {
                return false;
            }
            code_point = (code_point << 6U) | (continuation & 0x3FU);
        }
        if ((count == 3U && code_point < 0x800U) ||
            (count == 4U && code_point < 0x10000U) ||
            (code_point >= 0xD800U && code_point <= 0xDFFFU) ||
            code_point > 0x10FFFFU) {
            return false;
        }
        index += count;
    }
    return true;
}

}  // namespace aviutl2::live
