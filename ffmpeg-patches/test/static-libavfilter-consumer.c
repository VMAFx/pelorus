#include <libavfilter/avfilter.h>

int main(void)
{
    return avfilter_get_by_name("pelorus_scenecut") ? 0 : 1;
}
