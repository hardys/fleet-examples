# Fleet Examples Version Update Scripts

This directory contains scripts for managing and updating versions in the fleet-examples repository.

## fleet_versions_update.py

Automated script that updates fleet-examples files from the latest SUSE Edge release manifest.

### What it does

The script:
1. Fetches the latest (or specified) release manifest container image
2. Extracts component versions from the manifest
3. Renders Jinja2 templates with the extracted versions
4. Updates all fleet.yaml, plan files, and GitRepo references

### Prerequisites

```bash
pip install -r requirements.txt
```

### Usage

Update to the latest release manifest version:
```bash
python fleet_versions_update.py
```

Update to a specific version:
```bash
python fleet_versions_update.py --tag 3.5.0
```

Use a custom release manifest repository:
```bash
python fleet_versions_update.py --image registry.example.com/my-repo/release-manifest --tag 1.0.0
```

### Template Structure

Templates are located in the `templates/` directory and mirror the repository structure:

```
templates/
├── fleets/
│   └── day2/
│       ├── chart-templates/
│       │   ├── kubevirt/fleet.yaml.j2
│       │   ├── rancher/fleet.yaml.j2
│       │   └── ...
│       └── system-upgrade-controller-plans/
│           ├── k3s-upgrade/
│           ├── rke2-upgrade/
│           └── os-upgrade/
├── gitrepos/
│   └── day2/
└── bundles/
    └── day2/
```

Templates use Jinja2 syntax with variables like:
- `{{ edge_version }}` - Full Edge version (e.g., 3.5.0)
- `{{ k3s_version }}` - K3s version
- `{{ rke2_version }}` - RKE2 version
- `{{ kubevirt_version }}` - KubeVirt Helm chart version
- `{{ rancher_version }}` - Rancher version
- And many more...

### Adding New Templates

1. Create a `.j2` file in the appropriate `templates/` subdirectory
2. Use Jinja2 variables for version values
3. The output path will be the template path minus `templates/` prefix and `.j2` suffix

Example:
- Template: `templates/fleets/day2/chart-templates/new-chart/fleet.yaml.j2`
- Output: `fleets/day2/chart-templates/new-chart/fleet.yaml`

### Automation

The repository includes a GitHub Actions workflow (`.github/workflows/auto-update-versions.yml`) that:
- Runs daily to check for new release manifest versions
- Automatically creates a PR with version updates
- Labels PRs for easy identification

## Other Scripts

- `edge-save-images.sh` - Save Edge container images to a tar archive
- `edge-load-images.sh` - Load container images from a tar archive
- `generate-chart-upgrade-data.sh` - Generate upgrade data for Helm charts
