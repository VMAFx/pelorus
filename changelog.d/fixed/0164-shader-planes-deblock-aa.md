- Fixed the `deblock` and `aa` Vulkan filters to write every plane on formats
  whose full-size plane follows a subsampled one (BUG-028). The `deblock`
  plane loop and the `aa` fast=0 loop ended the invocation at the first plane
  whose size excluded the position, so on `yuva420p` the alpha plane outside
  the chroma extent was never written. Each plane is now bounds-checked on its
  own with `continue`. The `aa` fast=1 path already guarded per plane, so its
  workgroup barriers stay uniform. Luma and chroma output is unchanged.
