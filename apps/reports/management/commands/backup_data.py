from django.core.management.base import BaseCommand, CommandError

from apps.exams.models import Examination
from apps.reports.backup import backup_exam, full_backup, restore_exam


class Command(BaseCommand):
    help = "Create a full backup, back up one examination, or restore an examination backup."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--full", action="store_true", help="Database + all data files.")
        group.add_argument("--exam", metavar="UUID", help="Back up one examination.")
        group.add_argument("--restore", metavar="ZIP", help="Restore an examination backup.")

    def handle(self, *args, **opts):
        if opts["full"]:
            path = full_backup()
        elif opts["exam"]:
            exam = Examination.objects.filter(uid=opts["exam"]).first()
            if not exam:
                raise CommandError("No examination with that id.")
            path = backup_exam(exam)
        else:
            from django.core.exceptions import ValidationError

            try:
                exam = restore_exam(opts["restore"])
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages))
            self.stdout.write(self.style.SUCCESS(f"Restored “{exam.name}”."))
            return
        self.stdout.write(self.style.SUCCESS(f"Backup written to {path}"))
