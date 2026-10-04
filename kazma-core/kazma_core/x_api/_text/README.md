Vendored twitter-text-parser 3.0.0 (MIT), https://github.com/swen128/twitter-text-python.
Source: PyPI wheel twitter-text-parser 3.0.0. Upstream ports Twitter text v3 weighted validation.
Local changes: relative package imports; importlib.resources instead of removed pkg_resources;
module-private helper names; narrow Unicode-error handling for IDNA conversion.
The package initializer does not shadow its parse_tweet submodule.
Emoji assets retain upstream Unicode notices; unknown newer emoji are conservatively overcounted.
Client previews and all server publish paths use this same server parser.
