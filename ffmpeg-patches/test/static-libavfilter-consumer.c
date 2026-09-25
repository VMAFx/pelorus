/* Copyright 2026 Lusoris. BSD-2-Clause-Patent. */

#include <libavfilter/avfilter.h>

int main(void)
{
    return avfilter_get_by_name("pelorus_scenecut") ? 0 : 1;
}
