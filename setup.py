import setuptools


with open('README.md', encoding='utf-8') as f:
    long_description = f.read()


setuptools.setup(
    name='django-feed-reader',
    version='2.0.1-beta.2',
    description='An RSS feed reading library for Django.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author='Gareth Simpson',
    author_email='g@xurble.org',
    url='https://github.com/xurble/django-feed-reader',
    license='MIT',
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Framework :: Django",
        "Framework :: Django :: 3.2",
        "Framework :: Django :: 4.2",
        "Framework :: Django :: 5.0",
        "Framework :: Django :: 5.1",
    ],
    install_requires=[
        'dripfeed-client',
        'sgmllib3k',
        'requests',
        'feedparser>=6.0.0',
        'beautifulsoup4',
        'pyrfc3339',
        'Django>=3.2',
    ],
    extras_require={
        'test': [
            'pytest>=7.0',
            'pytest-django>=4.5',
            'requests-mock>=1.10',
        ],
    },
    include_package_data=True,
)
