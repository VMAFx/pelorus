- Hardened the shared x265 CSV conformance fixture so a permissive process
  umask cannot create group- or world-accessible test data, and pre-existing
  paths cannot be overwritten through the fixed fixture name.
