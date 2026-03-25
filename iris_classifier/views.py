import logging
import csv
import pandas as pd
import io

from django.shortcuts import render, redirect
from django.views.generic import TemplateView, ListView, FormView, View
from django.contrib import messages
from django.urls import reverse_lazy
from django.http import HttpResponse
from sklearn.datasets import load_iris

from .models import PredictionHistory
from .forms import IrisPredictionForm, TrainingDataUploadForm
from .ml_model import get_classifier, IrisClassifier

logger = logging.getLogger(__name__)


class IndexView(TemplateView):
    """iris_classifier のホームページ"""
    template_name = 'iris_classifier/index.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_predictions'] = PredictionHistory.objects.count()
        
        # 最近の予測履歴（ログインユーザーのみ）
        if self.request.user.is_authenticated:
            context['recent_predictions'] = PredictionHistory.objects.filter(
                user=self.request.user
            )[:5]
        
        return context


class DownloadDatasetView(View):
    """Irisデータセットをダウンロードするビュー"""
    
    def get(self, request, *args, **kwargs):
        """GETリクエストでIrisデータセットをCSVとしてダウンロード"""
        try:
            # scikit-learnからIrisデータセットを取得
            iris = load_iris()
            
            # HTTPレスポンスを作成（CSV形式）
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="iris_dataset.csv"'
            
            # CSVライターを作成
            writer = csv.writer(response)
            
            # ヘッダー行を書き込み
            writer.writerow(['sepal_length', 'sepal_width', 'petal_length', 'petal_width', 'species'])
            
            # データ行を書き込み
            target_names = ['Setosa', 'Versicolor', 'Virginica']
            for features, target in zip(iris.data, iris.target):
                row = list(features) + [target_names[target]]
                writer.writerow(row)
            
            logger.info('Iris dataset downloaded')
            return response
            
        except Exception as e:
            logger.error(f'Error downloading dataset: {e}')
            messages.error(request, 'データセットのダウンロード中にエラーが発生しました。')
            return redirect('iris_classifier:analysis')


class RetrainModelView(FormView):
    """モデル再トレーニングビュー（CSVアップロード対応）"""
    template_name = 'iris_classifier/analysis.html'
    form_class = TrainingDataUploadForm
    success_url = reverse_lazy('iris_classifier:analysis')
    
    def form_valid(self, form):
        """フォームが有効な場合の処理"""
        try:
            logger.info('Starting model retraining with uploaded CSV...')
            
            # アップロードされたファイルを取得
            uploaded_file = form.cleaned_data['training_data']
            
            # CSVファイルを読み込み
            file_content = uploaded_file.read().decode('utf-8')
            df = pd.read_csv(io.StringIO(file_content))
            
            logger.info(f'Loaded CSV with {len(df)} samples')
            
            # 新しいモデルをトレーニング
            classifier = IrisClassifier()
            accuracy = classifier.train(custom_data=df)
            
            # グローバルインスタンスをリセット（新しいモデルを読み込むため）
            global _classifier_instance
            _classifier_instance = None
            
            messages.success(
                self.request,
                f'アップロードされたデータでモデルの再トレーニングが完了しました！'
                f'サンプル数: {len(df)}, 精度: {accuracy:.2%}'
            )
            logger.info(f'Model retrained successfully with {len(df)} samples, accuracy: {accuracy:.2%}')
            
        except ValueError as e:
            messages.error(self.request, f'データの形式が不正です: {str(e)}')
            logger.error(f'Invalid data format: {e}')
        except Exception as e:
            messages.error(self.request, f'モデルの再トレーニング中にエラーが発生しました: {str(e)}')
            logger.error(f'Model retraining error: {e}')
        
        return super().form_valid(form)
    
    def form_invalid(self, form):
        """フォームが無効な場合の処理"""
        messages.error(self.request, 'ファイルのアップロードに失敗しました。')
        return redirect('iris_classifier:analysis')


class PredictView(FormView):
    """アヤメ予測ページ"""
    template_name = 'iris_classifier/predict.html'
    form_class = IrisPredictionForm
    success_url = reverse_lazy('iris_classifier:predict')
    
    def form_valid(self, form):
        """フォームが有効な場合の処理"""
        try:
            # フォームの警告をチェック
            if '_warnings' in form.cleaned_data:
                for warning in form.cleaned_data['_warnings']:
                    messages.warning(self.request, warning)
            
            # 機械学習モデルによる予測処理
            prediction_result = self._ml_predict(form.cleaned_data)
            
            # 予測履歴を保存（ログインユーザーの場合のみ）
            user = self.request.user if self.request.user.is_authenticated else None
            prediction_history = PredictionHistory.objects.create(
                sepal_length=form.cleaned_data['sepal_length'],
                sepal_width=form.cleaned_data['sepal_width'],
                petal_length=form.cleaned_data['petal_length'],
                petal_width=form.cleaned_data['petal_width'],
                prediction=prediction_result['prediction'],
                probability=prediction_result['probability'],
                user=user
            )
            
            messages.success(
                self.request, 
                f'予測が完了しました！結果: {prediction_result["prediction"]} '
                f'(確率: {prediction_result["probability"]:.1%})'
            )
            
            if self.request.user.is_authenticated:
                logger.info('Prediction completed by {}: {} (probability: {:.1%})'.format(
                    self.request.user.email,
                    prediction_result['prediction'],
                    prediction_result['probability']
                ))
            else:
                logger.info('Prediction completed by anonymous user: {} (probability: {:.1%})'.format(
                    prediction_result['prediction'],
                    prediction_result['probability']
                ))
            
        except Exception as e:
            messages.error(self.request, '予測処理でエラーが発生しました。')
            logger.error('Prediction error: {}'.format(str(e)))
        
        return super().form_valid(form)
    
    def form_invalid(self, form):
        """フォームが無効な場合の処理"""
        messages.error(self.request, '入力内容に問題があります。')
        return super().form_invalid(form)
    
    def _ml_predict(self, data):
        """機械学習モデルによる予測"""
        try:
            # 機械学習モデルを取得
            classifier = get_classifier()
            
            # 予測を実行
            result = classifier.predict(
                sepal_length=data['sepal_length'],
                sepal_width=data['sepal_width'],
                petal_length=data['petal_length'],
                petal_width=data['petal_width']
            )
            
            return result
            
        except Exception as e:
            logger.error(f'Machine learning prediction error: {e}. Falling back to rule-based prediction.')
            # エラーが発生した場合はルールベース予測にフォールバック
            return self._dummy_predict(data)
    
    def _dummy_predict(self, data):
        """ダミー予測（フォールバック用）"""
        # 簡単なルールベース予測
        petal_length = data['petal_length']
        petal_width = data['petal_width']
        sepal_length = data['sepal_length']
        
        # より詳細なルールベース予測
        if petal_length < 2.0:
            return {'prediction': 'Setosa', 'probability': 0.95}
        elif petal_length < 4.8:
            if petal_width < 1.3:
                return {'prediction': 'Versicolor', 'probability': 0.85}
            else:
                return {'prediction': 'Virginica', 'probability': 0.75}
        else:
            if sepal_length < 6.0:
                return {'prediction': 'Versicolor', 'probability': 0.70}
            else:
                return {'prediction': 'Virginica', 'probability': 0.90}


class HistoryView(ListView):
    """予測履歴ページ"""
    model = PredictionHistory
    template_name = 'iris_classifier/history.html'
    context_object_name = 'predictions'
    paginate_by = 10
    
    def get_queryset(self):
        """ログインユーザーの予測履歴のみ取得"""
        if self.request.user.is_authenticated:
            return PredictionHistory.objects.filter(user=self.request.user).order_by('-created_at')
        else:
            return PredictionHistory.objects.none()


class AnalysisView(TemplateView):
    """データ分析ページ"""
    template_name = 'iris_classifier/analysis.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # 全体統計
        total_predictions = PredictionHistory.objects.count()
        setosa_count = PredictionHistory.objects.filter(prediction='Setosa').count()
        versicolor_count = PredictionHistory.objects.filter(prediction='Versicolor').count()
        virginica_count = PredictionHistory.objects.filter(prediction='Virginica').count()
        
        # ユーザー別統計（ログインユーザーのみ）
        user_predictions = None
        user_stats = None
        if self.request.user.is_authenticated:
            user_predictions = PredictionHistory.objects.filter(user=self.request.user).count()
            user_setosa = PredictionHistory.objects.filter(user=self.request.user, prediction='Setosa').count()
            user_versicolor = PredictionHistory.objects.filter(user=self.request.user, prediction='Versicolor').count()
            user_virginica = PredictionHistory.objects.filter(user=self.request.user, prediction='Virginica').count()
            
            user_stats = {
                'setosa': user_setosa,
                'versicolor': user_versicolor,
                'virginica': user_virginica,
                'setosa_percentage': (user_setosa / user_predictions * 100) if user_predictions > 0 else 0,
                'versicolor_percentage': (user_versicolor / user_predictions * 100) if user_predictions > 0 else 0,
                'virginica_percentage': (user_virginica / user_predictions * 100) if user_predictions > 0 else 0,
            }
        
        context.update({
            'analysis_data': {
                'total_predictions': total_predictions,
                'setosa_count': setosa_count,
                'versicolor_count': versicolor_count,
                'virginica_count': virginica_count,
                'setosa_percentage': (setosa_count / total_predictions * 100) if total_predictions > 0 else 0,
                'versicolor_percentage': (versicolor_count / total_predictions * 100) if total_predictions > 0 else 0,
                'virginica_percentage': (virginica_count / total_predictions * 100) if total_predictions > 0 else 0,
            },
            'user_predictions': user_predictions,
            'user_stats': user_stats,
        })
        
        return context
