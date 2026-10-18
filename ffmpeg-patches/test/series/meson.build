# libavcodec/nvenc_sei.h as the shared FFmpeg fix series creates it, written
# from the series patch into this build directory (ADR-0218); no copy of it is
# kept in this repository. The sei-fit test includes it through
# pelorus_sei_fit.h.
nvenc_sei_h = custom_target(
  'nvenc_sei.h',
  output: 'nvenc_sei.h',
  command: [python_prog, '-I', extract_series_file, ffmpeg_series_dir,
            'libavcodec/nvenc_sei.h', '@OUTPUT@'],
  depend_files: ffmpeg_series_files,
)
nvenc_sei_inc = include_directories('.')

subdir('planted')
