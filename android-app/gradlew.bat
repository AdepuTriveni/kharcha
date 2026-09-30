@echo off
set APP_HOME=%~dp0
"%JAVA_HOME%in\java.exe" %JAVA_OPTS% -Dorg.gradle.appname=gradlew -classpath "%APP_HOME%gradle\wrapper\gradle-wrapper.jar" org.gradle.wrapper.GradleWrapperMain %*
