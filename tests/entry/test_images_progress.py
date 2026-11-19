"""Automatic image build (dependency order), pulls, progress output, heartbeat, quiet mode and failure reporting of scripts/lib/common.sh."""
import re


def builds(fake):
    out = []
    for c in fake.calls():
        if c.startswith("docker build"):
            out.append(re.search(r"-t (\S+)", c).group(1))
    return out


def test_missing_image_is_built_and_present_one_is_left_alone(fake):
    fake.images("btbridge-lab:dev")
    r = fake.run("hb_ensure_image btbridge-lab:dev; echo rc=$?")
    assert "rc=0" in r.stdout and builds(fake) == [] and "present" in r.stdout


def test_dependent_image_builds_its_base_first_on_a_clean_machine(fake):
    r = fake.run("hb_ensure_image btbridge-hwkit:dev; echo rc=$?")          # the owner's failure: hwkit FROM btbridge-lab:dev
    assert "rc=0" in r.stdout
    assert builds(fake) == ["btbridge-lab:dev", "btbridge-hwkit:dev"]


def test_base_that_exists_is_not_rebuilt_for_a_dependent_image(fake):
    fake.images("btbridge-lab:dev")
    fake.run("hb_ensure_image btbridge-hwkit:dev")
    assert builds(fake) == ["btbridge-hwkit:dev"]


def test_always_refreshes_an_existing_image(fake):
    fake.images("hifi-demo:dev")
    r = fake.run("hb_ensure_image hifi-demo:dev --always")
    assert builds(fake) == ["hifi-demo:dev"] and "refreshing" in r.stdout


def test_build_uses_the_registry_dockerfile_context_and_pinned_build_args(fake):
    fake.run("hb_ensure_image btbridge-pw:trixie")
    call = [c for c in fake.calls() if c.startswith("docker build")][0]
    assert "-f " in call and "docker/Dockerfile.pipewire" in call and "--build-arg BASE=debian:trixie-slim@sha256:" in call


def test_progress_shows_why_and_the_build_steps_and_hides_noise(fake):
    r = fake.run("hb_steps 2; hb_step 'images'; hb_ensure_image btbridge-lab:dev")
    assert re.search(r"\[1/2 0:0\d\] images", r.stdout)
    assert "is missing - first run: lab runner" in r.stdout
    assert "#5 [2/3] RUN apt-get update" in r.stdout                        # step lines are shown ...
    assert "Get:1 http://deb.debian.org" not in r.stdout                    # ... apt chatter is not (it stays in the log)
    assert "build btbridge-lab:dev: done in" in r.stdout


def test_heartbeat_when_a_command_is_silent(fake):
    r = fake.run("hb_ensure_image btbridge-lab:dev", FAKE_BUILD_SLEEP=4, HB_HEARTBEAT=1)
    assert "still working" in r.stdout and "elapsed" in r.stdout and "last output" in r.stdout


def test_quiet_mode_prints_steps_only_but_keeps_the_log(fake):
    r = fake.run("hb_steps 1; hb_step 'images'; hb_ensure_image btbridge-lab:dev", HB_QUIET=1)
    assert r.stdout.strip().count("\n") == 0 and "[1/1" in r.stdout
    logs = list((fake.tmp / "logs").glob("*.log"))
    assert logs and "apt-get update" in logs[0].read_text()


def test_failed_build_shows_error_tail_log_path_and_fails(fake):
    r = fake.run("hb_ensure_images btbridge-hwkit:dev; echo rc=$?", FAKE_BUILD_FAIL="btbridge-lab:dev")
    assert "rc=1" in r.stdout
    assert "build btbridge-lab:dev failed" in r.stderr and "failed to solve" in r.stderr and "Full log:" in r.stderr
    assert builds(fake) == ["btbridge-lab:dev"]                             # the dependent image is not attempted


def test_unknown_image_name_is_an_error(fake):
    r = fake.run("hb_ensure_image nope:dev; echo rc=$?")
    assert "rc=2" in r.stdout and "unknown project image" in r.stderr


def test_third_party_images_are_pulled_once_with_reason(fake):
    ref = "lmscommunity/lyrionmusicserver:9.1.2@sha256:" + "a" * 64
    r = fake.run(f'hb_ensure_pulled "{ref}" "the real Lyrion server"; hb_ensure_pulled "{ref}"')
    assert "pulling lmscommunity/lyrionmusicserver:9.1.2 - the real Lyrion server" in r.stdout
    assert "Pulling from lmscommunity/lyrionmusicserver" in r.stdout and "abc123def456: Pull complete" in r.stdout
    assert len([c for c in fake.calls() if c.startswith("docker pull")]) == 1


def test_compose_images_are_pulled_except_project_images(fake):
    sha = "@sha256:" + "b" * 64
    r = fake.run("hb_pull_compose_images demo/compose.yml", FAKE_COMPOSE_IMAGES=f"hifi-demo:dev eclipse-mosquitto:2.0.20{sha} btbridge-lab:dev")
    pulls = [c for c in fake.calls() if c.startswith("docker pull")]
    assert pulls == [f"docker pull eclipse-mosquitto:2.0.20{sha}"], r.stderr


def test_every_registry_image_has_a_dockerfile_and_dependencies_are_registered(fake):
    r = fake.run('for i in $HB_ALL_IMAGES; do hb_image_info "$i" || echo "NOREG $i"; [ -f "$HB_ROOT/$HB_I_FILE" ] || echo "NOFILE $i"; '
                 'for d in $HB_I_DEPS; do hb_image_info "$d" || echo "NODEP $i $d"; done; done; echo checked')
    assert r.stdout.strip() == "checked", r.stdout
