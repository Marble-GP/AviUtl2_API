#pragma once

#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace aviutl2::live {

class Json final {
public:
    using Array = std::vector<Json>;
    using Object = std::map<std::string, Json, std::less<>>;
    using Value =
        std::variant<std::nullptr_t, bool, std::int64_t, double, std::string, Array, Object>;

    Json() noexcept;
    Json(std::nullptr_t) noexcept;
    Json(bool value) noexcept;
    Json(int value) noexcept;
    Json(std::int64_t value) noexcept;
    Json(double value) noexcept;
    Json(const char* value);
    Json(std::string value);
    Json(Array value);
    Json(Object value);

    [[nodiscard]] bool is_null() const noexcept;
    [[nodiscard]] bool is_bool() const noexcept;
    [[nodiscard]] bool is_integer() const noexcept;
    [[nodiscard]] bool is_number() const noexcept;
    [[nodiscard]] bool is_string() const noexcept;
    [[nodiscard]] bool is_array() const noexcept;
    [[nodiscard]] bool is_object() const noexcept;

    [[nodiscard]] bool as_bool() const;
    [[nodiscard]] std::int64_t as_integer() const;
    [[nodiscard]] double as_number() const;
    [[nodiscard]] const std::string& as_string() const;
    [[nodiscard]] const Array& as_array() const;
    [[nodiscard]] const Object& as_object() const;
    [[nodiscard]] Array& as_array();
    [[nodiscard]] Object& as_object();
    [[nodiscard]] const Json* find(std::string_view key) const noexcept;

    [[nodiscard]] const Value& value() const noexcept;

private:
    Value value_;
};

class JsonParseError final : public std::runtime_error {
public:
    JsonParseError(std::string message, std::size_t offset);

    [[nodiscard]] std::size_t offset() const noexcept;

private:
    std::size_t offset_;
};

[[nodiscard]] Json parse_json(std::string_view input);
[[nodiscard]] std::string serialize_json(const Json& value);
[[nodiscard]] bool is_valid_utf8(std::string_view input) noexcept;

}  // namespace aviutl2::live
