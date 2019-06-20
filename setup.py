import setuptools


with open('README.md', encoding='utf-8') as f:
    long_description = f.read()


setuptools.setup(
    name='django-feed-reader',
    version='0.0.3',
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
    ],
    install_requires=[
        'requests',
        'feedparser',
        'beautifulsoup4',
        'pyrfc3339',
        'Django>=2.0'
    ],
    include_package_data=True,
)

