from django import forms
from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.contrib.auth import get_user_model

from care.users.forms import UserChangeForm, UserCreationForm
from care.users.models import UserExternalIdentity, UserFlag
from care.utils.registries.feature_flag import FlagRegistry, FlagType

User = get_user_model()


class UserExternalIdentityInline(admin.TabularInline):
    """Administrative linking (ADR-0011 §5.2a).

    Read-mostly on purpose. A subject is immutable (rule §5.5), so correcting
    one is unlink-then-link rather than an edit -- an edit is
    indistinguishable from moving someone else's identity onto this account.
    """

    model = UserExternalIdentity
    fk_name = "user"
    extra = 0
    fields = ("provider_id", "issuer", "subject", "linked_by", "last_login_at")
    readonly_fields = ("last_login_at",)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return self.readonly_fields
        return (*self.readonly_fields, "provider_id", "issuer", "subject")


@admin.register(User)
class UserAdmin(auth_admin.UserAdmin):
    inlines = [UserExternalIdentityInline]
    form = UserChangeForm
    add_form = UserCreationForm
    actions = ["export_as_csv"]
    fieldsets = (
        (
            "User",
            {
                "fields": (
                    "phone_number",
                    "alt_phone_number",
                    "gender",
                    "verified",
                )
            },
        ),
        *auth_admin.UserAdmin.fieldsets,
    )
    list_display = ["username", "is_superuser"]
    search_fields = ["first_name", "last_name"]

    def get_queryset(self, request):
        # use the base manager to avoid filtering out soft deleted objects
        qs = self.model._base_manager.get_queryset()  # noqa: SLF001
        if ordering := self.get_ordering(request):
            qs = qs.order_by(*ordering)
        return qs


@admin.register(UserFlag)
class UserFlagAdmin(admin.ModelAdmin):
    class UserFlagForm(forms.ModelForm):
        flag = forms.ChoiceField(
            choices=lambda: FlagRegistry.get_all_flags_as_choices(FlagType.USER)
        )

        class Meta:
            fields = (
                "user",
                "flag",
            )
            model = UserFlag

    form = UserFlagForm
