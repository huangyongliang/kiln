// CI throwaway only. Reads the admin id and password from the environment.
// This file does not contain a password. Do not point it at the shared lab volume.
import hudson.security.FullControlOnceLoggedInAuthorizationStrategy
import hudson.security.HudsonPrivateSecurityRealm
import jenkins.install.InstallState
import jenkins.model.Jenkins

def username = System.getenv("JENKINS_ADMIN_ID")
def password = System.getenv("JENKINS_ADMIN_PASSWORD")
if (!username || !password) {
    throw new IllegalStateException("JENKINS_ADMIN_ID and JENKINS_ADMIN_PASSWORD are required")
}

def instance = Jenkins.instance
def realm = new HudsonPrivateSecurityRealm(false)
realm.createAccount(username, password)
instance.setSecurityRealm(realm)

def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
instance.setAuthorizationStrategy(strategy)
instance.setNumExecutors(2)
if (instance.getInstallState() != InstallState.INITIAL_SETUP_COMPLETED) {
    instance.setInstallState(InstallState.INITIAL_SETUP_COMPLETED)
}
instance.save()
