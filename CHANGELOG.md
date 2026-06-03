# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `add`: a positional text argument now takes priority over piped stdin.
  Previously, on non-interactive shells (sandboxes, cron, IDE tasks) an empty
  stdin silently overrode the argument and `koda a "hello"` aborted with
  "Empty content." When both an argument and stdin are supplied, the argument
  is used and a warning is printed to stderr. (#49)
