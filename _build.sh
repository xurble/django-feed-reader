rm dist/*
pip install --upgrade setuptools wheel twine
python setup.py sdist bdist_wheel
twine upload dist/*
rm -rf build
rm dist/*
