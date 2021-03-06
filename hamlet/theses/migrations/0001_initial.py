# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2017-10-19 19:24
from __future__ import unicode_literals

from django.db import migrations, models

class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Contribution',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('author', 'author'), ('advisor', 'advisor')], max_length=7)),
            ],
        ),
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('course', models.CharField(blank=True, max_length=10)),
            ],
        ),
        migrations.CreateModel(
            name='Person',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=75)),
            ],
        ),
        migrations.CreateModel(
            name='Thesis',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('degree', models.CharField(max_length=10)),
                ('url', models.URLField()),
                ('year', models.IntegerField()),
                ('identifier', models.IntegerField(db_index=True, help_text='The part after the final slash in things like http://hdl.handle.net/1721.1/39504', unique=True)),
                ('contributor', models.ManyToManyField(through='theses.Contribution', to='theses.Person')),
                ('department', models.ManyToManyField(to='theses.Department')),
            ],
            options={
                'verbose_name_plural': 'theses',
            },
        ),
        migrations.AddField(
            model_name='contribution',
            name='person',
            field=models.ForeignKey(on_delete=models.CASCADE, to='theses.Person'),
        ),
        migrations.AddField(
            model_name='contribution',
            name='thesis',
            field=models.ForeignKey(on_delete=models.CASCADE, to='theses.Thesis'),
        ),
    ]
