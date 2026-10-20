"""Tes tahap 19: berbagi device antar-pengguna dan grup (Viewer / Operator / Editor)."""
from django.urls import reverse

from activity.log import visible_events
from activity.models import ActivityEvent
from devices.models import Command, Device, Location, Variable
from devices.views import visible_devices
from rules.models import Alarm, RuleAction, RuleExecution
from rules.tests import RuleTestData, at

from . import access
from .models import DeviceShare, ShareGroup, ShareGroupMember

Role = DeviceShare.Role


class SharingData(RuleTestData):
    """siti memiliki Greenhouse Node (soil, pump); budi memiliki Budi's node; andi tamu."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from accounts.models import User

        cls.andi = User.objects.create_user("andi", password="rahasia-12345")
        cls.admin = User.objects.create_user("boss", password="rahasia-12345", role=User.Role.ADMIN)

    def share(self, role, user=None, group=None, device=None):
        return DeviceShare.objects.create(device=device or self.node, user=user, group=group, role=role,
                                          created_by=self.siti)

    def group(self, *members, owner=None, name="Team 3"):
        owner = owner or self.siti
        group = ShareGroup.objects.create(owner=owner, name=name)
        for user in (owner, *members):
            ShareGroupMember.objects.create(group=group, user=user)
        return group


class AccessTests(SharingData):
    def test_roles_direct_group_highest_wins(self):
        self.assertIsNone(access.role_for(self.budi, self.node))
        self.assertNotIn(self.node, access.devices_for(self.budi))
        self.share(Role.VIEWER, user=self.budi)
        self.assertEqual(access.role_for(self.budi, self.node), Role.VIEWER)
        self.assertIn(self.node, access.devices_for(self.budi))
        self.assertNotIn(self.node, access.devices_for(self.budi, access.OPERATOR))
        # Lewat grup sebagai Editor: peran tertinggi berlaku, dan device tidak muncul dua kali.
        self.share(Role.EDITOR, group=self.group(self.budi))
        self.assertEqual(access.role_for(self.budi, self.node), Role.EDITOR)
        self.assertEqual(list(access.devices_for(self.budi)).count(self.node), 1)
        self.assertEqual(access.role_for(self.siti, self.node), access.OWNER)
        self.assertEqual(access.role_for(self.admin, self.node), access.OWNER)
        self.assertNotIn(self.node, access.devices_for(self.budi, shared=False))
        self.assertEqual(access.roles(self.budi, [self.node, self.foreign]),
                         {self.node.pk: Role.EDITOR, self.foreign.pk: access.OWNER})

    def test_leaving_group_removes_access(self):
        group = self.group(self.budi)
        self.share(Role.OPERATOR, group=group)
        self.assertTrue(access.can(self.budi, self.node, access.OPERATOR))
        ShareGroupMember.objects.filter(group=group, user=self.budi).delete()
        self.assertIsNone(access.role_for(self.budi, self.node))


class PageTests(SharingData):
    def detail(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("devices:detail", args=[self.node.pk]))

    def test_no_share_is_404(self):
        self.assertEqual(self.detail(self.budi).status_code, 404)

    def test_viewer_sees_but_cannot_act(self):
        self.share(Role.VIEWER, user=self.budi)
        page = self.detail(self.budi)
        self.assertContains(page, "Shared with you · Viewer")
        self.assertContains(page, "View-only access")
        self.assertNotContains(page, self.node.access_token, msg_prefix="token hidden from viewers")
        self.assertNotContains(page, reverse("devices:edit", args=[self.node.pk]))
        self.assertNotContains(page, reverse("devices:command_add", args=[self.node.pk]))
        # Variabel dan chart boleh dilihat.
        self.assertEqual(self.client.get(reverse("devices:variable_detail", args=[self.node.pk, self.soil.pk])).status_code, 200)
        # Semua aksi ditolak di server (403), apa pun yang dikirim browser.
        posts = [
            (reverse("devices:variable_set", args=[self.node.pk, self.pump.pk]), {"value": "1"}),
            (reverse("devices:command_add", args=[self.node.pk]), {"method": "reboot", "params": "{}", "ttl": "300"}),
            (reverse("devices:edit", args=[self.node.pk]), {"name": "Hacked"}),
            (reverse("devices:token_regenerate", args=[self.node.pk]), {}),
            (reverse("devices:delete", args=[self.node.pk]), {}),
            (reverse("devices:variable_add", args=[self.node.pk]), {"key": "x"}),
            (reverse("sharing:share_add", args=[self.node.pk]), {"username": "andi", "role": "EDITOR"}),
        ]
        for url, data in posts:
            self.assertEqual(self.client.post(url, data).status_code, 403, url)
        self.pump.refresh_from_db()
        self.assertIsNone(self.pump.desired_value)
        self.assertFalse(Command.objects.exists())
        self.assertFalse(DeviceShare.objects.filter(user=self.andi).exists())

    def test_operator_controls_but_cannot_edit(self):
        self.share(Role.OPERATOR, user=self.budi)
        page = self.detail(self.budi)
        self.assertNotContains(page, "View-only access")
        self.assertNotContains(page, self.node.access_token)
        response = self.client.post(reverse("devices:variable_set", args=[self.node.pk, self.pump.pk]), {"value": "1"})
        self.assertEqual(response.status_code, 302)
        self.pump.refresh_from_db()
        self.assertEqual((self.pump.desired_value, self.pump.desired_by), (1, self.budi))
        self.client.post(reverse("devices:command_add", args=[self.node.pk]), {"method": "reboot", "params": "{}", "ttl": "300"})
        self.assertEqual(Command.objects.get().created_by, self.budi)
        self.assertEqual(self.client.get(reverse("devices:edit", args=[self.node.pk])).status_code, 403)

    def test_editor_edits_with_owner_locations_but_cannot_delete(self):
        greenhouse = Location.objects.create(owner=self.siti, name="Greenhouse", latitude=0, longitude=0)
        mine = Location.objects.create(owner=self.budi, name="Budi home", latitude=0, longitude=0)
        self.share(Role.EDITOR, user=self.budi)
        page = self.detail(self.budi)
        self.assertContains(page, self.node.access_token)
        form = self.client.get(reverse("devices:edit", args=[self.node.pk]))
        self.assertContains(form, "Greenhouse")
        self.assertNotContains(form, "Budi home")
        data = {"name": "Greenhouse Node 2", "device_type": "", "description": "", "location": greenhouse.pk, "board": "", "is_active": "on"}
        self.assertEqual(self.client.post(reverse("devices:edit", args=[self.node.pk]), data).status_code, 302)
        self.node.refresh_from_db()
        self.assertEqual((self.node.name, self.node.owner, self.node.location), ("Greenhouse Node 2", self.siti, greenhouse))
        data["location"] = mine.pk
        self.assertEqual(self.client.post(reverse("devices:edit", args=[self.node.pk]), data).status_code, 200)
        self.assertEqual(self.client.post(reverse("devices:delete", args=[self.node.pk])).status_code, 403)
        self.assertNotContains(page, reverse("devices:delete", args=[self.node.pk]))
        self.assertTrue(Device.objects.filter(pk=self.node.pk).exists())

    def test_device_list_shows_shared_devices(self):
        self.share(Role.OPERATOR, user=self.budi)
        self.client.force_login(self.budi)
        page = self.client.get(reverse("devices:list"))
        self.assertContains(page, "Greenhouse Node")
        self.assertContains(page, "Shared by siti · Operator")
        self.assertNotContains(self.client.get(reverse("devices:list") + "?access=mine"), "Greenhouse Node")
        shared = self.client.get(reverse("devices:list") + "?access=shared")
        self.assertContains(shared, "Greenhouse Node")
        self.assertNotContains(shared, "Budi&#x27;s node")

    def test_dashboard_controls_follow_role(self):
        self.share(Role.VIEWER, user=self.budi)
        self.client.force_login(self.budi)
        page = self.client.get(reverse("dashboard:index"))
        self.assertContains(page, "Water pump")
        self.assertContains(page, "View-only access")

    def test_alarms_need_operator(self):
        alarm = Alarm.objects.create(device=self.node, severity=Alarm.Severity.MAJOR, message="Dry")
        self.share(Role.VIEWER, user=self.budi)
        self.client.force_login(self.budi)
        self.assertContains(self.client.get(reverse("rules:alarms")), "Dry")
        self.assertEqual(self.client.post(reverse("rules:alarm_ack", args=[alarm.pk])).status_code, 403)
        DeviceShare.objects.update(role=Role.OPERATOR)
        self.client.post(reverse("rules:alarm_ack", args=[alarm.pk]))
        alarm.refresh_from_db()
        self.assertEqual(alarm.acknowledged_by, self.budi)


class ManageSharesTests(SharingData):
    def setUp(self):
        self.client.force_login(self.siti)

    def add(self, **data):
        return self.client.post(reverse("sharing:share_add", args=[self.node.pk]), {"role": "VIEWER", **data}, follow=True)

    def test_share_change_role_and_remove(self):
        self.assertContains(self.add(username="BUDI", role="OPERATOR"), "Shared Greenhouse Node with budi as Operator")
        share = DeviceShare.objects.get()
        self.assertEqual((share.user, share.role, share.created_by), (self.budi, Role.OPERATOR, self.siti))
        # Berbagi lagi ke orang yang sama = ganti peran, bukan baris kedua.
        self.add(username="budi", role="EDITOR")
        self.assertEqual(DeviceShare.objects.get().role, Role.EDITOR)
        self.client.post(reverse("sharing:share_role", args=[share.pk]), {"role": "VIEWER"})
        self.assertEqual(DeviceShare.objects.get().role, Role.VIEWER)
        page = self.client.get(reverse("devices:detail", args=[self.node.pk]))
        self.assertContains(page, 'id="sharing"')
        self.client.post(reverse("sharing:share_delete", args=[share.pk]))
        self.assertFalse(DeviceShare.objects.exists())

    def test_invalid_targets(self):
        self.assertContains(self.add(username="nobody"), "No active user called")
        self.assertContains(self.add(username="siti"), "already owns this device")
        self.assertContains(self.add(username="boss"), "is an Admin")
        self.assertContains(self.add(), "Type a username or choose a group")
        group = self.group(self.budi)
        self.assertContains(self.add(username="budi", group=group.pk), "one of them")
        # Grup orang lain yang tidak saya ikuti tidak bisa dipilih.
        other = self.group(owner=self.andi, name="Andi team")
        self.assertContains(self.add(group=other.pk), "Select a valid choice")
        self.assertContains(self.add(group=group.pk, role="OPERATOR"), "with group Team 3 as Operator")

    def test_recipient_can_leave_and_admin_can_manage(self):
        share = self.share(Role.VIEWER, user=self.budi)
        self.client.force_login(self.budi)
        self.assertContains(self.client.get(reverse("devices:detail", args=[self.node.pk])), "Leave")
        self.client.post(reverse("sharing:share_delete", args=[share.pk]))
        self.assertFalse(DeviceShare.objects.exists())
        self.client.force_login(self.admin)
        self.add(username="andi")
        self.assertTrue(DeviceShare.objects.filter(user=self.andi).exists())

    def test_other_user_cannot_touch_shares(self):
        share = self.share(Role.VIEWER, user=self.budi)
        self.client.force_login(self.andi)
        self.assertEqual(self.client.post(reverse("sharing:share_delete", args=[share.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("sharing:share_role", args=[share.pk]), {"role": "EDITOR"}).status_code, 403)
        self.assertEqual(DeviceShare.objects.get().role, Role.VIEWER)


class GroupTests(SharingData):
    def test_create_add_members_leave_and_delete(self):
        self.client.force_login(self.siti)
        self.client.post(reverse("sharing:group_add"), {"name": "Team 3", "description": ""})
        group = ShareGroup.objects.get()
        self.assertEqual(list(group.members.values_list("user__username", flat=True)), ["siti"])
        url = reverse("sharing:member_add", args=[group.pk])
        self.client.post(url, {"username": "budi"})
        self.assertContains(self.client.post(url, {"username": "budi"}, follow=True), "already a member")
        self.assertContains(self.client.post(reverse("sharing:group_add"), {"name": "team 3"}), "already have a group")
        self.share(Role.VIEWER, group=group)
        self.assertIn(self.node, visible_devices(self.budi))

        # budi melihat grupnya, tidak bisa mengelola, bisa keluar.
        self.client.force_login(self.budi)
        page = self.client.get(reverse("sharing:group_detail", args=[group.pk]))
        self.assertContains(page, "Greenhouse Node")
        self.assertNotContains(page, "Add member")
        self.assertEqual(self.client.post(url, {"username": "andi"}).status_code, 404)
        member = group.members.get(user=self.budi)
        self.client.post(reverse("sharing:member_remove", args=[group.pk, member.pk]))
        self.assertNotIn(self.node, visible_devices(self.budi))
        self.assertEqual(self.client.get(reverse("sharing:group_detail", args=[group.pk])).status_code, 404)

        # Pemilik tidak bisa dikeluarkan; menghapus grup menghapus share-nya.
        self.client.force_login(self.siti)
        owner = group.members.get(user=self.siti)
        self.assertContains(self.client.post(reverse("sharing:member_remove", args=[group.pk, owner.pk]), follow=True),
                            "cannot leave")
        self.client.post(reverse("sharing:group_delete", args=[group.pk]))
        self.assertFalse(DeviceShare.objects.exists())

    def test_deleting_user_or_group_removes_their_shares(self):
        """Regresi MySQL: CASCADE dari FK nullable dijalankan sebagai UPDATE SET NULL lalu DELETE."""
        self.share(Role.VIEWER, user=self.budi)
        self.share(Role.VIEWER, group=self.group(self.andi))
        ShareGroup.objects.all().delete()
        self.budi.delete()
        self.assertFalse(DeviceShare.objects.exists())

    def test_share_needs_user_or_group(self):
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError, transaction

        with self.assertRaises(ValidationError):
            DeviceShare(device=self.node, role=Role.VIEWER).clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DeviceShare.objects.create(device=self.node, user=self.budi, group=self.group(), role=Role.VIEWER)

    def test_group_list_only_my_groups(self):
        self.group(self.budi)
        self.group(owner=self.andi, name="Andi team")
        self.client.force_login(self.budi)
        page = self.client.get(reverse("sharing:group_list"))
        self.assertContains(page, "Team 3")
        self.assertNotContains(page, "Andi team")


class RuleSharingTests(SharingData):
    """Aturan milik budi memakai device siti yang dibagikan kepadanya."""

    def budi_rule(self, actions=None):
        return self.make_rule(owner=self.budi, actions=actions)

    def test_viewer_rule_reads_sensor_but_cannot_control(self):
        self.share(Role.VIEWER, user=self.budi)
        self.budi_rule()
        [execution] = self.send(15, at(0))
        self.assertEqual(execution.transition, RuleExecution.Transition.ACTIVATE)
        self.assertFalse(execution.ok)
        self.assertIn("no access", execution.results[0]["detail"])
        self.pump.refresh_from_db()
        self.assertIsNone(self.pump.desired_value)

    def test_operator_rule_controls_and_owner_is_told(self):
        self.share(Role.OPERATOR, user=self.budi)
        self.budi_rule()
        [execution] = self.send(15, at(0))
        self.assertTrue(execution.ok)
        self.pump.refresh_from_db()
        self.assertEqual(self.pump.desired_value, 1)
        note = ActivityEvent.objects.get(verb="rule.shared_action")
        self.assertEqual((note.owner, note.actor), (self.siti, self.budi))
        self.assertIn('Rule "Irrigation" of budi', note.message)

    def test_revoked_share_stops_the_rule(self):
        share = self.share(Role.OPERATOR, user=self.budi)
        self.budi_rule()
        self.send(25, at(0))
        share.delete()
        self.assertEqual(self.send(15, at(10)), [], "the condition is unknown without access")
        self.pump.refresh_from_db()
        self.assertIsNone(self.pump.desired_value)

    def test_alarm_on_shared_device_needs_operator(self):
        self.share(Role.VIEWER, user=self.budi)
        self.budi_rule(actions=[{"kind": RuleAction.Kind.ALARM, "message": "Dry"}])
        [execution] = self.send(15, at(0))
        self.assertIn("needs Operator access", execution.results[0]["detail"])
        self.assertFalse(Alarm.objects.exists())

    def test_form_choices_follow_role(self):
        self.share(Role.VIEWER, user=self.budi)
        self.client.force_login(self.budi)
        page = self.client.get(reverse("rules:new_model", args=["eca"]))
        content = page.content.decode()
        # Sensor siti boleh dipilih di kondisi; aktuatornya tidak boleh menjadi target aksi.
        self.assertIn(f'value="{self.soil.pk}"', content)
        target = content[content.find('name="actions-0-target_variable"'):]
        target = target[:target.find("</select>")]
        self.assertNotIn(f'value="{self.pump.pk}"', target)
        DeviceShare.objects.update(role=Role.OPERATOR)
        content = self.client.get(reverse("rules:new_model", args=["eca"])).content.decode()
        target = content[content.find('name="actions-0-target_variable"'):]
        self.assertIn(f'value="{self.pump.pk}"', target[:target.find("</select>")])


class ActivitySharingTests(SharingData):
    def test_recipient_sees_device_events_not_owner_private_ones(self):
        ActivityEvent.objects.all().delete()
        share = self.share(Role.OPERATOR, user=self.budi)
        rule = self.make_rule()
        self.send(15, at(0))
        events = visible_events(self.budi)
        verbs = set(events.values_list("verb", flat=True))
        self.assertIn("share.added", verbs)
        self.assertIn("actuator.set", verbs, "control of the shared device")
        self.assertNotIn("rule.activate", verbs, "siti's own rules stay private")
        self.assertNotIn("rule.created", verbs)
        share.delete()
        events = visible_events(self.budi)
        self.assertEqual(list(events.values_list("verb", flat=True)), ["share.removed"])
        self.assertIn("Your access to Greenhouse Node", events.get().message)
        self.assertTrue(visible_events(self.siti).filter(verb="share.removed", device=self.node).exists())
        self.assertTrue(rule.pk)

    def test_group_membership_is_logged_for_both(self):
        self.group(self.budi)
        self.assertTrue(visible_events(self.siti).filter(verb="group.member_added").exists())
        self.assertTrue(visible_events(self.budi).filter(verb="group.member_added",
                                                          message__contains="You were added").exists())
