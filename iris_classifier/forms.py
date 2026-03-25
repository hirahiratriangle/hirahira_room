"""
Iris Classifier フォーム
"""
from django import forms


class IrisPredictionForm(forms.Form):
    """アヤメ予測フォーム"""
    
    sepal_length = forms.FloatField(
        label='がく片の長さ (cm)',
        min_value=0.0,
        max_value=10.0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '例: 5.1',
            'step': '0.1'
        })
    )
    
    sepal_width = forms.FloatField(
        label='がく片の幅 (cm)',
        min_value=0.0,
        max_value=10.0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '例: 3.5',
            'step': '0.1'
        })
    )
    
    petal_length = forms.FloatField(
        label='花弁の長さ (cm)',
        min_value=0.0,
        max_value=10.0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '例: 1.4',
            'step': '0.1'
        })
    )
    
    petal_width = forms.FloatField(
        label='花弁の幅 (cm)',
        min_value=0.0,
        max_value=10.0,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'placeholder': '例: 0.2',
            'step': '0.1'
        })
    )
    
    def clean(self):
        cleaned_data = super().clean()
        
        # 一般的な範囲外の値に対して警告を追加
        warnings = []
        
        sepal_length = cleaned_data.get('sepal_length')
        sepal_width = cleaned_data.get('sepal_width')
        petal_length = cleaned_data.get('petal_length')
        petal_width = cleaned_data.get('petal_width')
        
        if sepal_length and (sepal_length < 4.0 or sepal_length > 8.0):
            warnings.append('がく片の長さが一般的な範囲（4.0-8.0cm）外です。')
        
        if sepal_width and (sepal_width < 2.0 or sepal_width > 4.5):
            warnings.append('がく片の幅が一般的な範囲（2.0-4.5cm）外です。')
        
        if petal_length and (petal_length < 1.0 or petal_length > 7.0):
            warnings.append('花弁の長さが一般的な範囲（1.0-7.0cm）外です。')
        
        if petal_width and (petal_width < 0.1 or petal_width > 2.5):
            warnings.append('花弁の幅が一般的な範囲（0.1-2.5cm）外です。')
        
        if warnings:
            cleaned_data['_warnings'] = warnings
        
        return cleaned_data


class TrainingDataUploadForm(forms.Form):
    """トレーニングデータアップロードフォーム"""
    
    training_data = forms.FileField(
        label='トレーニングデータ (CSV)',
        help_text='CSV形式: sepal_length, sepal_width, petal_length, petal_width, species',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv'
        })
    )
    
    def clean_training_data(self):
        file = self.cleaned_data.get('training_data')
        
        if not file:
            raise forms.ValidationError('ファイルを選択してください。')
        
        # ファイルサイズチェック（10MB以下）
        if file.size > 10 * 1024 * 1024:
            raise forms.ValidationError('ファイルサイズは10MB以下にしてください。')
        
        # ファイル拡張子チェック
        if not file.name.endswith('.csv'):
            raise forms.ValidationError('CSVファイルを選択してください。')
        
        return file
