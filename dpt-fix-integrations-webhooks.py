# dpt-fix-integrations-webhooks.py
#
# Tool to harmonize webhooks and integrations configurations of DPT repos

import logging
import os
import sys

import gitlab
from debian.deb822 import Deb822

logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout, level=logging.INFO)

SALSA_TOKEN = os.environ.get('SALSA_TOKEN', None)
if not SALSA_TOKEN:
    print('Set SALSA_TOKEN environment variable, exiting')
    sys.exit(1)

# 9360 is the group_id for python-team/packages subgroup, it could be automatically obtained
# from https://salsa.debian.org/api/v4/groups/python-team/subgroups/ but meh
GROUPID = 9360

logging.info("Gather DPT projects from Salsa")
salsa = gitlab.Gitlab('https://salsa.debian.org/', private_token=SALSA_TOKEN)
group = salsa.groups.get(GROUPID)
group_projects = group.projects.list(all=True, order_by='name', sort='asc', as_list=True)

stats = {
    'tagpending': 0,
    'kgb': 0,
    'emails-on-push': 0,
    'irker': 0,
    'exception': 0,
}

for group_project in group_projects:
    project = salsa.projects.get(group_project.id)
    logging.info(f'Processing {project.name}')

    # gather details
    try:

        set_tagpending = True
        set_kgb = True

        for hook in project.hooks.list():
            if hook.url.startswith('https://webhook.salsa.debian.org/tagpending/'):
                set_tagpending = False
                continue
            if hook.url.startswith('http://kgb.debian.net:9418'):
                # See https://salsa.debian.org/kgb-team/kgb/-/wikis/usage for details on the default arguments
                if hook.url == 'http://kgb.debian.net:9418/webhook/?channel=debian-python-changes':
                    set_kgb = False
                else:
                    project.hooks.delete(id=hook.id)
                    logging.info('  removed old-format KBG webhook')

        set_email = True

        for service in project.services.list():
            if service.title == 'Emails on push':
                set_email = False
                continue
            if service.title == 'Irker (IRC gateway)':
                project.services.delete(id=service.slug)
                logging.info('  removed integration: Irker')
                stats['irker'] += 1

        # make changes

        if set_tagpending:
            if any(x['name'] == 'debian' for x in project.repository_tree()):
                d_control_id = [d['id'] for d in project.repository_tree(path='debian', all=True) if d['name'] == 'control'][0]
                d_control = Deb822(project.repository_raw_blob(d_control_id))

                project.hooks.create({'url': f'https://webhook.salsa.debian.org/tagpending/{d_control["Source"]}'})
                logging.info('  added webhook: tagpending')
                stats['tagpending'] += 1
            else:
                logging.error('  unable to determine the source package name')

        if set_kgb:
            project.hooks.create({'url': 'http://kgb.debian.net:9418/webhook/?channel=debian-python-changes'})
            logging.info('  added webhook: KGB')
            stats['kgb'] += 1

        if set_email:
            project.services.update('emails-on-push', {'recipients': 'dispatch@tracker.debian.org'})
            logging.info('  added integration: email-on-push')
            stats['emails-on-push'] += 1
    except Exception as e:
        logging.exception(e)
        stats['exception'] += 1

logging.info('')
logging.info('Execution recap:')
for k, v in stats.items():
    logging.info(f'    {k}: {v}')
