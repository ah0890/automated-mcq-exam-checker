from django.core.management.base import BaseCommand

from apps.processing.worker import run_pending


class Command(BaseCommand):
    help = "Process all queued papers (resumes interrupted jobs). Safe to run at any time."

    def handle(self, *args, **options):
        n = run_pending()
        self.stdout.write(self.style.SUCCESS(f"Processed {n} queued job(s)."))
