#include "host_version.hpp"

#include <atomic>

namespace aviutl2::live {
namespace {

std::atomic<std::uint32_t> g_host_version{0U};

}  // namespace

void store_host_version(const std::uint32_t version) noexcept {
    g_host_version.store(version, std::memory_order_release);
}

std::uint32_t host_version() noexcept {
    return g_host_version.load(std::memory_order_acquire);
}

}  // namespace aviutl2::live
