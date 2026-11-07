from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator

User = get_user_model()


class PredictionHistory(models.Model):
    """アヤメ予測履歴モデル"""
    
    # 入力データ
    sepal_length = models.FloatField(
        'がく片の長さ (cm)',
        validators=[MinValueValidator(0.0), MaxValueValidator(10.0)]
    )
    sepal_width = models.FloatField(
        'がく片の幅 (cm)',
        validators=[MinValueValidator(0.0), MaxValueValidator(10.0)]
    )
    petal_length = models.FloatField(
        '花弁の長さ (cm)',
        validators=[MinValueValidator(0.0), MaxValueValidator(10.0)]
    )
    petal_width = models.FloatField(
        '花弁の幅 (cm)',
        validators=[MinValueValidator(0.0), MaxValueValidator(10.0)]
    )
    
    # 予測結果
    prediction = models.CharField('予測結果', max_length=50)
    probability = models.FloatField('予測確率')
    
    # メタデータ
    model_used = models.CharField('使用モデル', max_length=100, default='RandomForest')
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, verbose_name='ユーザー')
    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    
    class Meta:
        verbose_name = '予測履歴'
        verbose_name_plural = '予測履歴'
        ordering = ['-created_at']
    
    def __str__(self):
        return f'{self.prediction} ({self.probability:.2%}) - {self.created_at.strftime("%Y/%m/%d %H:%M")}'
    
    @property
    def input_features(self):
        """入力特徴量を辞書形式で返す"""
        return {
            'sepal_length': self.sepal_length,
            'sepal_width': self.sepal_width,
            'petal_length': self.petal_length,
            'petal_width': self.petal_width,
        }