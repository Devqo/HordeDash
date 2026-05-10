from flask import Blueprint, render_template, request, session, redirect, url_for, current_app

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    using_generated_password = current_app.config.get(
        'USING_GENERATED_PASSWORD', False)
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == current_app.config['UI_PASSWORD']:
            session['authenticated'] = True
            return redirect(url_for('views.index'))
        return render_template('login.html', error="Invalid password", using_generated_password=using_generated_password)
    return render_template('login.html', using_generated_password=using_generated_password)


@auth_bp.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('auth.login'))
