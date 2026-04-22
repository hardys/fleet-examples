#!/usr/bin/env python3
"""
Update fleet-examples files from release manifest in container image.
Uses ORAS library for OCI registry interaction.
"""

import oras.client
import requests
import tempfile
import tarfile
import gzip
import io
import yaml
import argparse
import re
import hashlib
import json
import platform
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined


def parse_version(version_str):
    """Parse version string into tuple for comparison."""
    version_str = version_str.lstrip('v')
    parts = re.findall(r'\d+', version_str)
    return tuple(int(p) for p in parts) if parts else (0,)


def get_latest_version_tag(repository):
    """Get the latest version tag from the repository."""
    client = oras.client.OrasClient()

    print(f"Discovering tags for {repository}")
    tags = client.get_tags(repository)

    version_tags = [
        tag for tag in tags
        if not tag.endswith(('.sig', '.att')) and re.match(r'^\d+\.\d+', tag)
    ]

    if not version_tags:
        raise ValueError(f"No version tags found in {repository}")

    version_tags.sort(key=parse_version, reverse=True)
    latest = version_tags[0]

    print(f"✓ Latest version: {latest}")
    return latest


def find_file_in_layer(layer_path, target_file):
    """Search for a file in a gzipped tar layer."""
    with open(layer_path, 'rb') as f:
        layer_data = f.read()

    try:
        stream = io.BytesIO(gzip.decompress(layer_data))
    except:
        stream = io.BytesIO(layer_data)

    try:
        with tarfile.open(fileobj=stream, mode='r') as tar:
            for member in tar.getmembers():
                if member.name.lstrip('/') == target_file.lstrip('/'):
                    f = tar.extractfile(member)
                    return f.read().decode('utf-8') if f else None
    except:
        pass
    return None


def get_manifest_with_fallback(client, image_ref):
    """Get manifest with proper Accept headers for manifest lists."""
    if '@' in image_ref:
        repo, ref = image_ref.rsplit('@', 1)
    elif ':' in image_ref:
        repo, ref = image_ref.rsplit(':', 1)
    else:
        repo = image_ref
        ref = 'latest'

    registry = repo.split('/')[0]
    repository_path = '/'.join(repo.split('/')[1:])
    manifest_url = f"https://{registry}/v2/{repository_path}/manifests/{ref}"

    headers = {
        'Accept': ', '.join([
            'application/vnd.docker.distribution.manifest.list.v2+json',
            'application/vnd.oci.image.index.v1+json',
            'application/vnd.docker.distribution.manifest.v2+json',
            'application/vnd.oci.image.manifest.v1+json',
        ])
    }

    response = requests.get(manifest_url, headers=headers)
    response.raise_for_status()
    return response.json()


def fetch_release_manifest(image_ref):
    """Fetch release manifest from container image using ORAS."""
    print(f"Fetching release manifest from {image_ref}")

    manifest_data = {}
    client = oras.client.OrasClient()

    try:
        manifest = get_manifest_with_fallback(client, image_ref)
    except Exception as e:
        print(f"  ⚠ Warning: Failed to get manifest with custom headers, trying ORAS client: {e}")
        manifest = client.get_manifest(image_ref)

    if manifest.get('mediaType') in [
        'application/vnd.docker.distribution.manifest.list.v2+json',
        'application/vnd.oci.image.index.v1+json'
    ]:
        machine = platform.machine().lower()
        arch_map = {
            'x86_64': 'amd64',
            'amd64': 'amd64',
            'aarch64': 'arm64',
            'arm64': 'arm64',
            'armv7l': 'arm',
            'armv6l': 'arm',
        }
        target_arch = arch_map.get(machine, machine)
        target_os = 'linux'

        print(f"✓ Detected multi-platform image, selecting {target_arch}/{target_os} platform")

        platform_manifest = None
        for m in manifest.get('manifests', []):
            p = m.get('platform', {})
            if p.get('architecture') == target_arch and p.get('os') == target_os:
                platform_manifest = m
                break

        if not platform_manifest:
            platform_manifest = manifest['manifests'][0]
            fallback_platform = platform_manifest.get('platform', {})
            print(f"  ⚠ {target_arch}/{target_os} not found, using {fallback_platform.get('architecture')}/{fallback_platform.get('os')}")

        repo = image_ref.split(':')[0].split('@')[0]
        digest = platform_manifest['digest']
        image_ref = f"{repo}@{digest}"
        print(f"✓ Resolved to {digest}")

    with tempfile.TemporaryDirectory() as tmpdir:
        layers = client.pull(target=image_ref, outdir=tmpdir)

        if not layers:
            raise ValueError(f"No layers found in {image_ref}")

        for i, layer_path in enumerate(layers):
            content = find_file_in_layer(layer_path, "release_manifest.yaml")
            if content:
                print(f"✓ Found release_manifest.yaml in layer {i+1}")
                manifest_data["release_manifest"] = yaml.safe_load(content)

    if "release_manifest" not in manifest_data:
        raise ValueError(f"release_manifest.yaml not found in any layer of {image_ref}")

    return manifest_data


def extract_chart_version(helm_list, release_name):
    """Extract chart version from helm list by release name."""
    for helm in helm_list:
        if helm.get('releaseName') == release_name:
            return helm.get('version', '')
    return ''


def strip_upstream_version(version):
    """Strip +upX.Y.Z suffix from version."""
    return version.split('+up')[-1] if '+up' in version else version


def render_yaml_template(template_path, data, output_path):
    """Render a single YAML file from template."""
    template_file = Path(template_path)

    if not template_file.exists():
        print(f"  ⚠ Template not found: {template_path}")
        return

    env = Environment(
        loader=FileSystemLoader(template_file.parent),
        undefined=StrictUndefined,
        keep_trailing_newline=True
    )
    template = env.get_template(template_file.name)

    output = template.render(data)

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(output)
    print(f"✓ Generated {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Update fleet-examples files from release manifest'
    )
    parser.add_argument(
        '--image',
        default='registry.opensuse.org/isv/suse/edge/factory/test_manifest_images/release-manifest',
        help='Container image reference without tag'
    )
    parser.add_argument(
        '--tag',
        help='Specific tag to use (if not specified, discovers latest version)'
    )
    parser.add_argument(
        '--templates-dir',
        default='templates',
        help='Directory containing Jinja2 templates (default: templates)'
    )

    args = parser.parse_args()

    if ':' in args.image:
        repository, image_tag = args.image.rsplit(':', 1)
    else:
        repository = args.image
        image_tag = None

    if args.tag:
        tag = args.tag
    elif image_tag:
        tag = image_tag
    else:
        tag = get_latest_version_tag(repository)

    image_ref = f"{repository}:{tag}"

    # Fetch release manifest data
    manifest_data_all = fetch_release_manifest(image_ref)
    manifest_data = manifest_data_all["release_manifest"]
    spec = manifest_data.get('spec', {})

    # Extract versions
    edge_version = spec.get('releaseVersion', '')
    edge_version_short = '.'.join(edge_version.split('.')[:2])

    components = spec.get('components', {})
    kubernetes = components.get('kubernetes', {})
    k3s_version = kubernetes.get('k3s', {}).get('version', '')
    rke2_version = kubernetes.get('rke2', {}).get('version', '')
    os_version = components.get('operatingSystem', {}).get('version', '')

    helm_charts = components.get('workloads', {}).get('helm', [])

    # Build template data
    data = {
        'edge_version': edge_version,
        'edge_version_short': edge_version_short,
        'k3s_version': k3s_version,
        'rke2_version': rke2_version,
        'os_version': os_version,
        'rancher_version': extract_chart_version(helm_charts, 'rancher'),
        'longhorn_version': extract_chart_version(helm_charts, 'longhorn'),
        'longhorn_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'longhorn')),
        'neuvector_version': extract_chart_version(helm_charts, 'neuvector'),
        'neuvector_crd_version': extract_chart_version(helm_charts, 'neuvector-crd'),
        'kubevirt_version': extract_chart_version(helm_charts, 'kubevirt'),
        'kubevirt_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'kubevirt')),
        'kubevirt_dashboard_version': extract_chart_version(helm_charts, 'kubevirt-dashboard-extension'),
        'cdi_version': extract_chart_version(helm_charts, 'cdi'),
        'cdi_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'cdi')),
        'metallb_version': extract_chart_version(helm_charts, 'metallb'),
        'metallb_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'metallb')),
        'metal3_version': extract_chart_version(helm_charts, 'metal3'),
        'metal3_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'metal3')),
        'elemental_operator_version': extract_chart_version(helm_charts, 'elemental-operator'),
        'elemental_crd_version': extract_chart_version(helm_charts, 'elemental-operator-crds'),
        'elemental_dashboard_version': extract_chart_version(helm_charts, 'elemental-dashboard-extension'),
        'endpoint_copier_version': extract_chart_version(helm_charts, 'endpoint-copier-operator'),
        'endpoint_copier_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'endpoint-copier-operator')),
        'sriov_version': extract_chart_version(helm_charts, 'sriov-network-operator'),
        'sriov_crd_version': extract_chart_version(helm_charts, 'sriov-crd'),
        'sriov_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'sriov-network-operator')),
        'turtles_providers_version': extract_chart_version(helm_charts, 'rancher-turtles-providers'),
        'turtles_providers_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'rancher-turtles-providers')),
        'cert_manager_version': extract_chart_version(helm_charts, 'cert-manager'),
        'cert_manager_crd_version': extract_chart_version(helm_charts, 'cert-manager-crds'),
        'upgrade_controller_version': extract_chart_version(helm_charts, 'upgrade-controller'),
        'upgrade_controller_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'upgrade-controller')),
        'akri_version': extract_chart_version(helm_charts, 'akri'),
        'akri_upstream': strip_upstream_version(extract_chart_version(helm_charts, 'akri')),
        'akri_dashboard_version': extract_chart_version(helm_charts, 'akri-dashboard-extension'),
    }

    print(f"\n✓ Extracted versions for Edge {edge_version}")

    # Render all templates
    templates_dir = Path(args.templates_dir)
    if not templates_dir.exists():
        print(f"\n⚠ Templates directory not found: {templates_dir}")
        print("  Please create templates with .j2 extension matching the repository structure")
        return

    # Find and render all .j2 templates
    for template_file in templates_dir.rglob('*.j2'):
        # Calculate output path (remove templates/ prefix and .j2 suffix)
        rel_path = template_file.relative_to(templates_dir)
        output_path = str(rel_path)[:-3]  # Remove .j2

        try:
            render_yaml_template(template_file, data, output_path)
        except Exception as e:
            print(f"  ✗ Error rendering {template_file}: {e}")

    print(f"\n✓ Completed version updates for Edge {edge_version}")


if __name__ == "__main__":
    main()
