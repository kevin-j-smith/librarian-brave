# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

First release on PyPI.

- `BraveBackend`, registered under the `librarian.search_backends` entry
  point as `brave`, answering the `web` category from the Brave Search API.
- Sends nothing until `BRAVE_API_KEY` is set. A local monthly count
  (`BRAVE_MONTHLY_QUOTA`) and Brave's own rate-limit headers stop requests
  before a quota is overspent; refusals are reported as `degraded`.

[Unreleased]: https://github.com/kevin-j-smith/librarian-brave/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kevin-j-smith/librarian-brave/releases/tag/v0.1.0
