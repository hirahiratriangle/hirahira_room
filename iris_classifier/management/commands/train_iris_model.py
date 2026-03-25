"""
アヤメ分類モデルをトレーニングするDjango管理コマンド
"""
from django.core.management.base import BaseCommand
from iris_classifier.ml_model import IrisClassifier


class Command(BaseCommand):
    help = 'Train the Iris classification machine learning model'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting model training...'))
        
        # モデルのトレーニング
        classifier = IrisClassifier()
        accuracy = classifier.train()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Model training completed successfully!'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Model accuracy: {accuracy:.2%}'
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Model saved to: {classifier.model_path}'
            )
        )
