/*
 * Minimal stand-in for libavutil/frame.h so pelorus_sidedata_test.c can compile
 * the real ffmpeg-patches/files/pelorus_sidedata.h without an FFmpeg tree. Only
 * the members the header reads are declared; the layout is NOT FFmpeg's ABI.
 */
#ifndef PELORUS_TEST_STUB_FRAME_H
#define PELORUS_TEST_STUB_FRAME_H

#include <stddef.h>
#include <stdint.h>

enum AVFrameSideDataType {
    AV_FRAME_DATA_OTHER_STUB = 0,
    AV_FRAME_DATA_SEI_UNREGISTERED = 1,
};

typedef struct AVFrameSideData {
    enum AVFrameSideDataType type;
    uint8_t *data;
    size_t size;
} AVFrameSideData;

typedef struct AVFrame {
    AVFrameSideData **side_data;
    int nb_side_data;
} AVFrame;

#endif
