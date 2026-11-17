from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(role='admin', is_superuser=False).update(role='manager', is_staff=True)
    User.objects.filter(is_superuser=True).update(role='admin', is_staff=True)


def backwards(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(role='manager').update(role='admin', is_superuser=False)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_alter_user_role'),
    ]

    operations = [
        migrations.RunPython(forwards, reverse_code=backwards),
    ]
