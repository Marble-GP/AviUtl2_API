set(BRIDGE_SOURCES
    src/alias_tools.cpp
    src/bridge_state.cpp
    src/command_dispatcher.cpp
    src/frame_codec.cpp
    src/instance_registry.cpp
    src/json.cpp
    src/logger.cpp
    src/pipe_server.cpp
    src/protocol.cpp
    src/sdk_adapter.cpp
)

add_library(AviUtl2LiveBridge MODULE
    src/plugin.cpp
    ${BRIDGE_SOURCES}
)

target_compile_features(AviUtl2LiveBridge PRIVATE cxx_std_20)
target_include_directories(AviUtl2LiveBridge PRIVATE
    include
    "${PROJECT_SOURCE_DIR}/third_party/aviutl2_sdk/include/aviutl2_sdk"
)
target_compile_definitions(AviUtl2LiveBridge PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)
target_compile_options(AviUtl2LiveBridge PRIVATE
    /W4
    /WX
    /permissive-
    /EHsc
    /utf-8
)
target_link_libraries(AviUtl2LiveBridge PRIVATE
    advapi32
    bcrypt
    ole32
    windowscodecs
)
set_target_properties(AviUtl2LiveBridge PROPERTIES
    PREFIX ""
    SUFFIX ".aux2"
)

if(AVIUTL2_LIVE_BRIDGE_BUILD_TESTS)
    add_executable(AviUtl2LiveBridgeTests
        tests/native_tests.cpp
        src/alias_tools.cpp
        src/command_dispatcher.cpp
        src/frame_codec.cpp
        src/instance_registry.cpp
        src/json.cpp
        src/logger.cpp
        src/pipe_server.cpp
        src/protocol.cpp
        src/sdk_adapter.cpp
    )
    target_compile_features(AviUtl2LiveBridgeTests PRIVATE cxx_std_20)
    target_include_directories(AviUtl2LiveBridgeTests PRIVATE
        include
        "${PROJECT_SOURCE_DIR}/third_party/aviutl2_sdk/include/aviutl2_sdk"
    )
    target_compile_definitions(AviUtl2LiveBridgeTests PRIVATE
        UNICODE
        _UNICODE
        WIN32_LEAN_AND_MEAN
        NOMINMAX
        AVIUTL2_FIXTURE_DIR="${PROJECT_SOURCE_DIR}/protocol/fixtures"
    )
    target_compile_options(AviUtl2LiveBridgeTests PRIVATE
        /W4
        /WX
        /permissive-
        /EHsc
        /utf-8
    )
    target_link_libraries(AviUtl2LiveBridgeTests PRIVATE
        advapi32
        bcrypt
        ole32
        windowscodecs
    )
    add_test(NAME AviUtl2LiveBridgeTests COMMAND AviUtl2LiveBridgeTests)
endif()
