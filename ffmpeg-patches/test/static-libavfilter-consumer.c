/*
 * Copyright 2026 Lusoris
 *
 * SPDX-License-Identifier: EUPL-1.2
 */

#include <libavfilter/avfilter.h>

int main(void)
{
    return avfilter_get_by_name("pelorus_scenecut") ? 0 : 1;
}
