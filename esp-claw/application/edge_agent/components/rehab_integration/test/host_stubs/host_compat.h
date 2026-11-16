#pragma once

#include <io.h>
#include <sys/stat.h>
#include <time.h>

time_t rehab_test_time(time_t *destination);

static inline struct tm *rehab_test_localtime_r(const time_t *source,
                                                struct tm *destination)
{
    return localtime_s(destination, source) == 0 ? destination : NULL;
}

#define localtime_r rehab_test_localtime_r
#define time rehab_test_time
#define mkdir(path, mode) (mkdir)(path)
#define fsync _commit
