import logging
import sys
from collections import defaultdict
from datetime import timedelta

import gitlab
import requests
import requests_cache
from debian.deb822 import Deb822
from debian.debian_support import Version

__version__ = '0.1.3'


def get_sid_version(srcpkg):
    # get the current version in Sid, from madison
    # example output:
    #
    # https://qa.debian.org/madison.php?package=matplotlib&text=on&s=sid&a=source,all,amd64
    # matplotlib | 3.5.0-1 | sid | source
    madison = requests.get(f"https://qa.debian.org/madison.php?package={srcpkg}&text=on&s=sid&a=source,all,amd64")
    if not madison.text:
        return None

    try:
        # rough but gets the job done
        return Version(madison.text.splitlines()[0].split(' | ')[1].strip())
    except Exception as e:
        logging.exception(e)
        return None


logging.basicConfig(format='%(asctime)s %(message)s', stream=sys.stdout, level=logging.DEBUG)

# TODO: remove, this is only for development
requests_cache.install_cache(
    'dpt_repos_check_cache',
    cache_control=False,
    expire_after=timedelta(days=15),
    backend='filesystem',
    serializer='json',
)

# 9360 is the group_id for python-team/packages subgroup, it could be automatically obtained
# from https://salsa.debian.org/api/v4/groups/python-team/subgroups/ but meh
GROUPID = 9360

logging.info("Gather DPT projects from Salsa")
salsa = gitlab.Gitlab('https://salsa.debian.org/')
group = salsa.groups.get(GROUPID)
# group_projects = group.projects.list(all=True, order_by='name', sort='asc', as_list=True)  TODO: uncomment
group_projects = group.projects.list(page=1, order_by='name', sort='asc', as_list=True)

violations = defaultdict(list)

# TODO: pristine-tar: contains .delta for latest upload to archive
# TODO: pristine-tar: onbtain the tarball and compare with the archive
# TODO: upstream: verify tag for latest upstream
# TODO: tags: tags for latest uploaded version in the changelog
# TODO: tags: latest upstream release
# TODO: latest upload to archive is in the git repo
# TODO: check for packages no longer in debian but with repo still in the team
# TODO: check for packages referring the team in maint/upl but with no repo in the team
# TODO: verify webhooks are set (FIRST: print whhich ones are set, as i guess there's more than kgb or tagpending?) https://salsa.debian.org/python-team/packages/astroid/-/hooks +
#       https://salsa.debian.org/python-team/packages/sqlmodel/-/hooks
#       --> requires auth! project.hooks.list()

for group_project in group_projects:
    project = salsa.projects.get(group_project.id)
    logging.info(f"CHECKING {project.name}...")

    # Branches checks

    branches = {x.name for x in project.branches.list()}

    if not branches:
        violations[project.name].append('ERROR: appears to be an empty repository')
        continue

    # DEP-14 is the recommendation doc for git layout: https://dep-team.pages.debian.net/deps/dep14/
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

    d_control_id = [d['id'] for d in project.repository_tree(path='debian', all=True) if d['name'] == 'control'][0]
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

    if not (vcs_browser := d_control.get('Vcs-Browser')):
        violations[project.name].append(f'ERROR: Vcs-Browser field is missing from debian/control')
    elif vcs_browser != project.web_url:
        violations[project.name].append(f'ERROR: Vcs-Browser field {vcs_browser} doesnt match the repo url {project.web_url}')
    if not (vcs_git := d_control.get('Vcs-Git')):
        violations[project.name].append(f'ERROR: Vcs-Git field is missing from debian/control')
    elif vcs_git != project.http_url_to_repo:
        violations[project.name].append(f'ERROR: Vcs-Git field {vcs_git} doesnt match the repo url {project.http_url_to_repo}')

    # debian/watch checks

    d_watch_id = [d['id'] for d in project.repository_tree(path='debian', all=True) if d['name'] == 'watch']
    if d_watch_id:
        d_watch = project.repository_raw_blob(d_watch_id[0]).decode().lower()

        if 'pypi.python.org' in d_watch or 'pypi.debian.net' in d_watch:
            violations[project.name].append('WARNING: debian/watch still uses PyPI to track new releases, https://lists.debian.org/debian-python/2021/06/msg00026.html')
    else:
        violations[project.name].append('ERROR: debian/watch is missing')

    # sid version check

    sid_version = get_sid_version(d_control["Source"])
    if not sid_version:
        violations[project.name].append('WARNING: unable to find a version in Sid: is this still in NEW/experimental-only?')

for pkg, viols in violations.items():
    print(pkg)
    for viol in viols:
        print(f"    {viol}")