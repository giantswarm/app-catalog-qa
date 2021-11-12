# App catalog quality assurance

A utility to check how app metadata and app repositories referenced in a catalog are doing with regard to some quality standards.

The result is printed as a Markdown checkbox list, suited for GitHub issues.

## Prerequisites

- Install requirements via `pip install -r requirements.txt` into your (virtual) Python environment.

- Check the provided `config.yaml` file and make sure `catalogs:` has the right catalog enabled. You can check multiple catalogs.

- Have a GitHub personal access token in a file (e. g. `~/.github-token`).

## Usage

Run the script like this:

```nohighlight
python cli.py 
```

You can optionally use the following command line flags:

- `--app-name`: Name of an app to check. If not provided, will check the entire catalog(s) configured.
- `--config`: Path to a configuration file. Default: `./config.yaml`.
- `--token-path`: Path to a token file. Default: `~/.github-token`.

The result will be printed to the console.

## Future improvements

More validations:

- Check whether the app is included in Changes & Releases in docs
- Validate kubeVersion
- Find app duplicates, also in several catalogs
- Too similar names in apps (grafana and grafana-app)
- Too many releases overall
- Too many releases per app
- Validate README
  - [x] Look for placeholder `{APP_NAME}`
  - [x] Amount of text
  - [ ] Check links
  - [ ] Check basic formatting, markdownlint
- Validate metadata referenced in application.giantswarm.io/metadata
- Validate schema referenced in application.giantswarm.io/values-schema
