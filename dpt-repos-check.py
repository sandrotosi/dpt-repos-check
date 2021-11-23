from collections import defaultdict

import gitlab
from debian.deb822 import Deb822

__version__ = '0.1.2'

# 9360 is the group_id for python-team/packages subgroup, it could be automatically obtained
# from https://salsa.debian.org/api/v4/groups/python-team/subgroups/ but meh
GROUPID = 9360

salsa = gitlab.Gitlab('https://salsa.debian.org/')
group = salsa.groups.get(GROUPID)
# group_projects = group.projects.list(all=True)  TODO: uncomment
group_projects = group.projects.list(page=1)

violations = defaultdict(list)

# TODO: pristine-tar: contains .delta for latest upload to archive
# TODO: pristine-tar: onbtain the tarball and compare with the archive
# TODO: upstream: verify tag for latest upstream
# TODO: tags: tags for latest uploaded version in the changelog
# TODO: tags: latest upstream release
# TODO: latest upload to archive is in the git repo
# TODO: check for packages no longer in debian but with repo still in the team
# TODO: check for packages referring the team in maint/upl but with no repo in the team
# TODO: packages using pypi (check upstream/metadata if it uses github and suggest to use that)
# TODO: verify webhooks are set (FIRST: print whhich ones are set, as i guess there's more than kgb or tagpending?) https://salsa.debian.org/python-team/packages/astroid/-/hooks +
#       https://salsa.debian.org/python-team/packages/sqlmodel/-/hooks
#       --> requires auth! project.hooks.list()

for group_project in sorted(group_projects, key=lambda p: p.attributes['name']):
    project = salsa.projects.get(group_project.id)
    print(f"CHECKING {project.name}...")

    # Branches checks

    branches = {x.name for x in project.branches.list()}

    if not branches:
        violations[project.name].append('ERROR: appears to be an empty repository')
        continue

    if not branches.intersection({'master', 'debian/master', 'debian/unstable', 'debian/latest'}):
        if branches.intersection({'sid', 'debian/sid'}):
            violations[project.name].append(f'WARNING: uncommon debian master branch (DEP-14); available branches={branches}')
        else:
            violations[project.name].append(f'ERROR: no valid Debian master branch; available branches={branches}')

    if not branches.intersection({'upstream', 'upstream/latest'}):
        violations[project.name].append(f'ERROR: no upstream branch; available branches={branches}')

    if 'pristine-tar' not in branches:
        violations[project.name].append(f'ERROR: no pristine-tar branch; available branches={branches}')

    # debian/control checks

    d_control_id = [d['id'] for d in project.repository_tree(path='debian') if d['name'] == 'control'][0]
    d_control = Deb822(project.repository_raw_blob(d_control_id))

    if project.name != d_control["Source"]:
        violations[project.name].append(f'ERROR: repo name "{project.name}" does not match the package source name "{d_control["Source"]}"')

    if 'Uploaders' not in d_control:
        violations[project.name].append('WARNING: Uploaders is missing from debian/control, that doesnt seem right')

    maints = d_control['Maintainer']+d_control.get('Uploaders', '')
    if all(
        x not in maints
        for x in (
            'team+python@tracker.debian.org',
            'python-apps-team@lists.alioth.debian.org',
            'python-modules-team@lists.alioth.debian.org',
        )
    ):
        violations[project.name].append('ERROR: DPT is not in Maintainer or Uploaders fields')
    elif 'team+python@tracker.debian.org' not in maints:
        violations[project.name].append('WARNING: still using the old team email address')

    if (vcs_browser := d_control['Vcs-Browser']) != project.web_url:
        violations[project.name].append(f'ERROR: Vcs-Browser field {vcs_browser} doesnt match the repo url {project.web_url}')
    if (vcs_git := d_control['Vcs-Git']) != project.http_url_to_repo:
        violations[project.name].append(f'ERROR: Vcs-Git field {vcs_git} doesnt match the repo url {project.http_url_to_repo}')

for pkg, viols in violations.items():
    print(pkg)
    for viol in viols:
        print(f"    {viol}")