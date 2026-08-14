from django.contrib.auth.views import LoginView
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse_lazy

class CustomLoginView(LoginView):
    """
    View de Login que valida a existência do usuário e a senha.
    Redireciona diretamente para o painel de projetos após o sucesso.
    """
    template_name = 'home.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        # REDIRECIONA PARA A PÁGINA DE PROJETOS APÓS O LOGIN
        return reverse_lazy('meus_projetos')

    def post(self, request, *args, **kwargs):
        """
        Intercepta o POST para converter o e-mail digitado no username correspondente
        antes de mandar para a validação interna do Django.
        """
        login_input = request.POST.get('username') or request.POST.get('login_usuario_ou_email', '')
        login_input = login_input.strip()

        if '@' in login_input:
            user_por_email = User.objects.filter(email__iexact=login_input).first()
            if user_por_email:
                request.POST = request.POST.copy()
                request.POST['username'] = user_por_email.username

        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        login(self.request, user)
        messages.success(self.request, f'Bem-vindo de volta, {user.first_name or user.username}!')
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        messages.error(
            self.request, 
            'Credenciais inválidas: Usuário/E-mail não cadastrado ou senha incorreta.'
        )
        return redirect('/?login_error=1')