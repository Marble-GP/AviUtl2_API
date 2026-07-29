#pragma once

#include <cstdint>
#include <filesystem>
#include <string>

namespace aviutl2::live {

class InstanceRegistry final {
public:
    InstanceRegistry(std::uint32_t pid, std::wstring pipe_name);
    ~InstanceRegistry();

    InstanceRegistry(const InstanceRegistry&) = delete;
    InstanceRegistry& operator=(const InstanceRegistry&) = delete;

    [[nodiscard]] bool publish(int scene_id, std::string& error_message);
    void remove() noexcept;
    [[nodiscard]] const std::filesystem::path& path() const noexcept;

private:
    std::uint32_t pid_;
    std::wstring pipe_name_;
    std::filesystem::path path_;
    bool published_ = false;
};

}  // namespace aviutl2::live
