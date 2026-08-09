cmake_minimum_required(VERSION 3.25)

project(
    AviUtl2API
    VERSION 0.9.6
    LANGUAGES CXX
)

option(AVIUTL2_LIVE_BRIDGE_BUILD_TESTS "Build native Live Bridge tests" ON)

if(NOT WIN32)
    message(FATAL_ERROR "AviUtl2LiveBridge is currently supported on Windows only")
endif()

enable_testing()
add_subdirectory(native/AviUtl2LiveBridge)
