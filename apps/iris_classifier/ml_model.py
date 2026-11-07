"""
機械学習モデルのトレーニングと予測
"""
import os
import pickle
import numpy as np
import pandas as pd
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import logging

logger = logging.getLogger(__name__)


class IrisClassifier:
    """アヤメ分類機械学習モデル"""
    
    def __init__(self, model_path=None):
        self.model = None
        self.model_path = model_path or self._get_default_model_path()
        self.target_names = ['Setosa', 'Versicolor', 'Virginica']
        
    def _get_default_model_path(self):
        """デフォルトのモデル保存パスを取得（本番環境対応）"""
        # Azure App Serviceの場合は永続化ストレージ(/home)を使用
        if os.getenv('WEBSITE_HOSTNAME'):
            # 本番環境（Azure App Service）
            model_dir = '/home/site/wwwroot/iris_classifier/models'
        else:
            # ローカル開発環境
            base_dir = os.path.dirname(os.path.abspath(__file__))
            model_dir = os.path.join(base_dir, 'models')
        
        return os.path.join(model_dir, 'iris_model.pkl')
    
    def train(self, custom_data=None):
        """
        アヤメデータセットを使ってモデルをトレーニング
        
        Parameters:
        -----------
        custom_data : pd.DataFrame, optional
            カスタムトレーニングデータ（指定されない場合はscikit-learnのデータを使用）
        """
        if custom_data is not None:
            # カスタムデータを使用
            X, y = self._prepare_custom_data(custom_data)
        else:
            # scikit-learnのアヤメデータセットをロード
            iris = load_iris()
            X = iris.data
            y = iris.target
        
        # 訓練データとテストデータに分割
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42
        )
        
        # RandomForestモデルの作成とトレーニング
        self.model = RandomForestClassifier(
            n_estimators=100,
            random_state=42,
            max_depth=5
        )
        self.model.fit(X_train, y_train)
        
        # テストデータで精度を確認
        y_pred = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)
        
        logger.info(f'Model trained with accuracy: {accuracy:.2%}')
        
        # モデルを保存
        self.save_model()
        
        return accuracy
    
    def _prepare_custom_data(self, df):
        """
        カスタムCSVデータを準備
        
        Parameters:
        -----------
        df : pd.DataFrame
            CSVから読み込んだデータフレーム
            
        Returns:
        --------
        X : np.ndarray
            特徴量データ
        y : np.ndarray
            ターゲットデータ
        """
        # 必要なカラムの確認
        required_columns = ['sepal_length', 'sepal_width', 'petal_length', 'petal_width', 'species']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            raise ValueError(f'必須カラムが不足しています: {", ".join(missing_columns)}')
        
        # 特徴量データを抽出
        X = df[['sepal_length', 'sepal_width', 'petal_length', 'petal_width']].values
        
        # 品種名を数値に変換
        species_mapping = {
            'Setosa': 0,
            'setosa': 0,
            'Versicolor': 1,
            'versicolor': 1,
            'Virginica': 2,
            'virginica': 2
        }
        
        y = df['species'].map(species_mapping).values
        
        # 不正な品種名のチェック
        if np.any(pd.isna(y)):
            raise ValueError('不正な品種名が含まれています。Setosa, Versicolor, Virginica のいずれかを指定してください。')
        
        return X, y
    
    def save_model(self):
        """モデルをファイルに保存"""
        # ディレクトリが存在しない場合は作成
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        
        with open(self.model_path, 'wb') as f:
            pickle.dump(self.model, f)
        
        logger.info(f'Model saved to {self.model_path}')
    
    def load_model(self):
        """保存されたモデルをロード"""
        if not os.path.exists(self.model_path):
            logger.warning(f'Model file not found at {self.model_path}. Training new model...')
            self.train()
            return
        
        try:
            with open(self.model_path, 'rb') as f:
                self.model = pickle.load(f)
            logger.info(f'Model loaded from {self.model_path}')
        except Exception as e:
            logger.error(f'Error loading model: {e}. Training new model...')
            self.train()
    
    def predict(self, sepal_length, sepal_width, petal_length, petal_width):
        """
        アヤメの品種を予測
        
        Parameters:
        -----------
        sepal_length : float
            がく片の長さ (cm)
        sepal_width : float
            がく片の幅 (cm)
        petal_length : float
            花弁の長さ (cm)
        petal_width : float
            花弁の幅 (cm)
            
        Returns:
        --------
        dict
            prediction: 予測された品種名
            probability: 予測確率
            probabilities: 各品種の確率
        """
        # モデルがロードされていない場合はロード
        if self.model is None:
            self.load_model()
        
        # 入力データを配列に変換
        X = np.array([[sepal_length, sepal_width, petal_length, petal_width]])
        
        # 予測
        prediction_idx = self.model.predict(X)[0]
        prediction_name = self.target_names[prediction_idx]
        
        # 各クラスの予測確率
        probabilities = self.model.predict_proba(X)[0]
        max_probability = probabilities[prediction_idx]
        
        return {
            'prediction': prediction_name,
            'probability': float(max_probability),
            'probabilities': {
                name: float(prob) 
                for name, prob in zip(self.target_names, probabilities)
            }
        }
    
    def get_feature_importance(self):
        """特徴量の重要度を取得"""
        if self.model is None:
            self.load_model()
        
        feature_names = ['sepal_length', 'sepal_width', 'petal_length', 'petal_width']
        importances = self.model.feature_importances_
        
        return {
            name: float(importance) 
            for name, importance in zip(feature_names, importances)
        }


# グローバルなモデルインスタンス
_classifier_instance = None


def get_classifier():
    """シングルトンパターンでClassifierインスタンスを取得"""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = IrisClassifier()
        _classifier_instance.load_model()
    return _classifier_instance
