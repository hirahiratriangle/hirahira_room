from django import forms

from .models import CountEvent


class CountEventForm(forms.ModelForm):
    class Meta:
        model = CountEvent
        fields = ('name', 'date', 'memo', 'notify',)
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'memo': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].input_formats = ['%Y-%m-%d']

        for name, field in self.fields.items():
            # チェックボックスに form-control を当てると見た目が崩れる
            if name == 'notify':
                field.widget.attrs['class'] = 'form-check-input'
            else:
                field.widget.attrs['class'] = 'form-control'

        self.fields['name'].widget.attrs['placeholder'] = '例）結婚記念日、資格試験'
        self.fields['memo'].widget.attrs['placeholder'] = 'メモ（任意）'
