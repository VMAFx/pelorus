# The same header with NVENC_HEVC_SEI_RESERVE planted from 256 to 255: the
# sei-fit-planted test builds sei_fit_test.c against it and must fail, so a
# series header that drifts from the frozen fit rules turns the suite red
# (ADR-0218).
nvenc_sei_planted_h = custom_target(
  'nvenc_sei.h (planted)',
  output: 'nvenc_sei.h',
  command: [python_prog, '-I', extract_series_file, ffmpeg_series_dir,
            'libavcodec/nvenc_sei.h', '@OUTPUT@', '--plant', 'reserve'],
  depend_files: ffmpeg_series_files,
)
nvenc_sei_planted_inc = include_directories('.')
