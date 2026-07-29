#include "frame_codec.hpp"

#include <windows.h>

#include <bcrypt.h>
#include <objidl.h>
#include <wincodec.h>
#include <wrl/client.h>

#include <array>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace aviutl2::live {
namespace {

using Microsoft::WRL::ComPtr;

class ComApartment final {
public:
    ComApartment() noexcept {
        const HRESULT result =
            CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        initialized_ = result == S_OK || result == S_FALSE;
        usable_ = initialized_ || result == RPC_E_CHANGED_MODE;
    }

    ~ComApartment() {
        if (initialized_) {
            CoUninitialize();
        }
    }

    [[nodiscard]] bool usable() const noexcept {
        return usable_;
    }

private:
    bool initialized_ = false;
    bool usable_ = false;
};

[[nodiscard]] bool succeeded(
    const HRESULT result,
    std::string& error_message,
    const char* const operation) {
    if (SUCCEEDED(result)) {
        return true;
    }
    error_message = std::string(operation) + " failed.";
    return false;
}

}  // namespace

bool encode_png_rgba(
    const int width,
    const int height,
    const std::span<const std::uint8_t> rgba,
    std::vector<std::uint8_t>& png,
    std::string& error_message) noexcept {
    try {
        png.clear();
        if (width <= 0 || height <= 0) {
            error_message = "The PNG dimensions are invalid.";
            return false;
        }
        const std::size_t stride =
            static_cast<std::size_t>(width) * 4U;
        const std::size_t expected =
            stride * static_cast<std::size_t>(height);
        if (rgba.size() != expected ||
            stride > std::numeric_limits<UINT>::max() ||
            expected > std::numeric_limits<UINT>::max()) {
            error_message = "The PNG RGBA buffer size is invalid.";
            return false;
        }

        ComApartment apartment;
        if (!apartment.usable()) {
            error_message = "COM could not be initialized for PNG encoding.";
            return false;
        }

        ComPtr<IWICImagingFactory> factory;
        if (!succeeded(
                CoCreateInstance(
                    CLSID_WICImagingFactory,
                    nullptr,
                    CLSCTX_INPROC_SERVER,
                    IID_PPV_ARGS(&factory)),
                error_message,
                "WIC factory creation")) {
            return false;
        }
        ComPtr<IStream> stream;
        if (!succeeded(
                CreateStreamOnHGlobal(nullptr, TRUE, &stream),
                error_message,
                "PNG memory stream creation")) {
            return false;
        }
        ComPtr<IWICBitmapEncoder> encoder;
        if (!succeeded(
                factory->CreateEncoder(
                    GUID_ContainerFormatPng,
                    nullptr,
                    &encoder),
                error_message,
                "PNG encoder creation") ||
            !succeeded(
                encoder->Initialize(
                    stream.Get(),
                    WICBitmapEncoderNoCache),
                error_message,
                "PNG encoder initialization")) {
            return false;
        }
        ComPtr<IWICBitmapFrameEncode> frame;
        ComPtr<IPropertyBag2> properties;
        if (!succeeded(
                encoder->CreateNewFrame(
                    &frame,
                    &properties),
                error_message,
                "PNG frame creation") ||
            !succeeded(
                frame->Initialize(properties.Get()),
                error_message,
                "PNG frame initialization") ||
            !succeeded(
                frame->SetSize(
                    static_cast<UINT>(width),
                    static_cast<UINT>(height)),
                error_message,
                "PNG size setup")) {
            return false;
        }
        std::vector<std::uint8_t> bgra(
            rgba.begin(),
            rgba.end());
        for (std::size_t offset = 0U;
             offset < bgra.size();
             offset += 4U) {
            std::swap(bgra[offset], bgra[offset + 2U]);
        }
        WICPixelFormatGUID pixel_format =
            GUID_WICPixelFormat32bppBGRA;
        if (!succeeded(
                frame->SetPixelFormat(&pixel_format),
                error_message,
                "PNG pixel format setup") ||
            pixel_format != GUID_WICPixelFormat32bppBGRA) {
            error_message =
                "WIC did not accept the BGRA PNG pixel format.";
            return false;
        }
        if (!succeeded(
                frame->WritePixels(
                    static_cast<UINT>(height),
                    static_cast<UINT>(stride),
                    static_cast<UINT>(expected),
                    bgra.data()),
                error_message,
                "PNG pixel write") ||
            !succeeded(
                frame->Commit(),
                error_message,
                "PNG frame commit") ||
            !succeeded(
                encoder->Commit(),
                error_message,
                "PNG encoder commit")) {
            return false;
        }

        HGLOBAL global = nullptr;
        if (!succeeded(
                GetHGlobalFromStream(stream.Get(), &global),
                error_message,
                "PNG memory access") ||
            global == nullptr) {
            return false;
        }
        STATSTG statistics{};
        if (!succeeded(
                stream->Stat(
                    &statistics,
                    STATFLAG_NONAME),
                error_message,
                "PNG stream size query")) {
            return false;
        }
        const ULONGLONG stream_size =
            statistics.cbSize.QuadPart;
        const SIZE_T allocated_size = GlobalSize(global);
        if (stream_size == 0U ||
            stream_size >
                static_cast<ULONGLONG>(allocated_size)) {
            error_message = "The encoded PNG size is invalid.";
            return false;
        }
        const std::size_t size =
            static_cast<std::size_t>(stream_size);
        const void* const bytes = GlobalLock(global);
        if (bytes == nullptr || size == 0U) {
            error_message = "The encoded PNG memory is unavailable.";
            if (bytes != nullptr) {
                GlobalUnlock(global);
            }
            return false;
        }
        png.assign(
            static_cast<const std::uint8_t*>(bytes),
            static_cast<const std::uint8_t*>(bytes) + size);
        GlobalUnlock(global);
        return true;
    } catch (const std::exception& error) {
        error_message = error.what();
        return false;
    } catch (...) {
        error_message = "The RGBA frame could not be encoded as PNG.";
        return false;
    }
}

std::string sha256_hex(
    const std::span<const std::uint8_t> bytes) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    std::array<std::uint8_t, 32U> digest{};
    if (BCryptOpenAlgorithmProvider(
            &algorithm,
            BCRYPT_SHA256_ALGORITHM,
            nullptr,
            0) < 0) {
        throw std::runtime_error("SHA-256 provider initialization failed");
    }
    const auto close_algorithm = [&algorithm] {
        if (algorithm != nullptr) {
            BCryptCloseAlgorithmProvider(algorithm, 0);
        }
    };
    if (BCryptCreateHash(
            algorithm,
            &hash,
            nullptr,
            0,
            nullptr,
            0,
            0) < 0) {
        close_algorithm();
        throw std::runtime_error("SHA-256 hash initialization failed");
    }
    if ((!bytes.empty() &&
         BCryptHashData(
             hash,
             const_cast<PUCHAR>(bytes.data()),
             static_cast<ULONG>(bytes.size()),
             0) < 0) ||
        BCryptFinishHash(
            hash,
            digest.data(),
            static_cast<ULONG>(digest.size()),
            0) < 0) {
        BCryptDestroyHash(hash);
        close_algorithm();
        throw std::runtime_error("SHA-256 calculation failed");
    }
    BCryptDestroyHash(hash);
    close_algorithm();

    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (const std::uint8_t byte : digest) {
        output << std::setw(2) << static_cast<unsigned int>(byte);
    }
    return output.str();
}

std::string base64_encode(
    const std::span<const std::uint8_t> bytes) {
    static constexpr char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789+/";
    std::string output;
    output.reserve(((bytes.size() + 2U) / 3U) * 4U);
    for (std::size_t index = 0U; index < bytes.size(); index += 3U) {
        const std::uint32_t first = bytes[index];
        const std::uint32_t second =
            index + 1U < bytes.size() ? bytes[index + 1U] : 0U;
        const std::uint32_t third =
            index + 2U < bytes.size() ? bytes[index + 2U] : 0U;
        const std::uint32_t value =
            (first << 16U) | (second << 8U) | third;
        output.push_back(alphabet[(value >> 18U) & 0x3FU]);
        output.push_back(alphabet[(value >> 12U) & 0x3FU]);
        output.push_back(
            index + 1U < bytes.size()
                ? alphabet[(value >> 6U) & 0x3FU]
                : '=');
        output.push_back(
            index + 2U < bytes.size()
                ? alphabet[value & 0x3FU]
                : '=');
    }
    return output;
}

}  // namespace aviutl2::live
