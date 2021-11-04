# App catalog quality assurance

A Python script to check how our app catalogs are doing

## TODO

More validations:

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
