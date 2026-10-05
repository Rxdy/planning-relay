"""F3 : un mail par semaine touchée, qui montre la semaine entière.

Une modification en semaine 42 ne renvoie que la semaine 42 ; une semaine qui
vient d'être publiée part en un mail « publiée ». Chaque mail a une version
texte et une version HTML (jours modifiés surlignés, ancien créneau barré).
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage
from html import escape

from .config import Config
from .modeles import Changement, Creneau

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
ETIQUETTES = {"ajout": "Ajouté", "modif": "Modifié", "suppr": "Annulé"}


@dataclass(frozen=True)
class Mail:
    objet: str
    texte: str
    html: str


def par_semaine(changements: list[Changement]) -> dict[date, list[Changement]]:
    semaines: dict[date, list[Changement]] = {}
    for ch in changements:
        semaines.setdefault(ch.jour - timedelta(days=ch.jour.weekday()), []).append(ch)
    return dict(sorted(semaines.items()))


def _periode(lundi: date) -> str:
    dimanche = lundi + timedelta(days=6)
    if lundi.month == dimanche.month:
        return f"{lundi.day}–{dimanche.day} {MOIS[lundi.month - 1]}"
    return f"{lundi.day} {MOIS[lundi.month - 1]} – {dimanche.day} {MOIS[dimanche.month - 1]}"


def _publiee(lundi: date, connus: dict[date, Creneau]) -> bool:
    """L'agenda n'avait encore rien pour cette semaine."""
    return not any(lundi <= j <= lundi + timedelta(days=6) for j in connus)


def objet(cfg: Config, lundi: date, chs: list[Changement], publiee: bool) -> str:
    sem = f"sem. {lundi.isocalendar().week} ({_periode(lundi)})"
    if publiee:
        etat = "publiée"
    else:
        etat = f"{len(chs)} changement{'s' if len(chs) > 1 else ''}"
    return f"Planning de {cfg.personne} · {sem} · {etat}"


def _jour(j: date) -> str:
    return f"{JOURS[j.weekday()]} {j:%d/%m}"


def _creneau(c: Creneau | None) -> str:
    # Un jour sans créneau est un repos : R et RF ne sont pas reportés.
    return c.libelle() if c else "Repos"


def _horaires(c: Creneau) -> str:
    if c.journee_entiere:
        return "journée entière"
    lendemain = " (lendemain)" if c.fin_le_lendemain else ""
    return f"{c.debut:%H:%M}–{c.fin:%H:%M}{lendemain}"


def _complet(c: Creneau) -> str:
    return f"{c.libelle()} ({c.intitule})" if c.intitule else c.libelle()


def details(ch: Changement) -> list[str]:
    """Ce qui a changé ce jour-là, en phrases courtes."""
    if ch.type == "ajout":
        return [f"Repos → {_complet(ch.apres)}"]
    if ch.type == "suppr":
        return [f"{_complet(ch.avant)} → Repos"]
    a, b = ch.avant, ch.apres
    lignes = []
    if a.code != b.code:
        intitules = f" ({a.intitule} → {b.intitule})" if a.intitule and b.intitule and a.intitule != b.intitule else ""
        lignes.append(f"Poste : {a.code} → {b.code}{intitules}")
    if (a.debut, a.fin) != (b.debut, b.fin):
        lignes.append(f"Horaires : {_horaires(a)} → {_horaires(b)}")
    if a.duree and b.duree and a.duree != b.duree:
        lignes.append(f"Durée : {a.duree} → {b.duree}")
    return lignes


def texte(cfg: Config, lundi: date, planning: dict[date, Creneau], chs: list[Changement],
          aujourdhui: date, publiee: bool) -> str:
    par_jour = {ch.jour: ch for ch in chs}
    entete = f"Planning de {cfg.personne}, semaine {lundi.isocalendar().week} ({_periode(lundi)})"
    lignes = [entete, "=" * len(entete), ""]
    if publiee:
        lignes += ["Nouvelle semaine publiée.", ""]
    else:
        lignes.append("CE QUI CHANGE")
        for ch in chs:
            lignes.append(f"- {_jour(ch.jour)} · {ETIQUETTES[ch.type]}")
            lignes += [f"    {d}" for d in details(ch)]
        lignes += ["", "LA SEMAINE"]
    for i in range(7):
        j = lundi + timedelta(days=i)
        c = planning.get(j)
        marque = "*" if j in par_jour and not publiee else " "
        ligne = f"{marque} {_jour(j)}   {_creneau(c):<18} {(c.intitule or '') if c else ''}".rstrip()
        if j < aujourdhui:
            ligne += "   [passé]"
        lignes.append(ligne)
    lignes += ["", "Postes : Matin (début avant 12h) · Soir (12h–20h) · Nuit (après 20h)"]
    if not publiee:
        lignes.append("* = jour qui a changé")
    return "\n".join(lignes)


COULEURS = {"ajout": "#1a7f37", "modif": "#a66b00", "suppr": "#b42318"}

# Postes classés par heure de début, pour couvrir aussi les codes inconnus.
# Pas les couleurs de Silae : la nuit y est rouge, comme « Annulé » ici.
POSTES = {"Matin": "#2e9e4f", "Soir": "#ef7d1a", "Nuit": "#3b4cc0", "Autre": "#8a8a8a"}


def poste(c: Creneau) -> str:
    if c.journee_entiere:
        return "Autre"
    h = c.debut.hour
    return "Matin" if 4 <= h < 12 else "Soir" if 12 <= h < 20 else "Nuit"


def _pastille(couleur: str) -> str:
    return (f"<span style='display:inline-block;width:10px;height:10px;border-radius:2px;"
            f"background:{couleur};margin-right:6px;vertical-align:baseline'></span>")


def legende(planning: dict[date, Creneau], lundi: date, aujourdhui: date, publiee: bool) -> str:
    semaine = [planning[j] for j in sorted(planning) if lundi <= j <= lundi + timedelta(days=6)]
    codes: dict[str, list[str]] = {}
    for c in semaine:
        codes.setdefault(poste(c), [])
        if c.code not in codes[poste(c)]:
            codes[poste(c)].append(c.code)
    postes = "".join(
        f"<span style='margin-right:14px;white-space:nowrap'>{_pastille(POSTES[p])}{p} "
        f"<span style='color:#888'>({escape(', '.join(cs))})</span></span>"
        for p, cs in sorted(codes.items(), key=lambda kv: list(POSTES).index(kv[0]))
    )
    reperes = []
    if not publiee:
        reperes.append("<span style='background:#fff4cc;padding:0 4px'>surligné</span> jour qui a changé")
        reperes.append("<s>barré</s> ancien créneau")
    if lundi < aujourdhui:
        reperes.append("<span style='color:#999'>gris</span> jour passé")
    ligne_reperes = f"<div style='margin-top:6px'>{' · '.join(reperes)}</div>" if reperes else ""
    return (
        "<div style='font-size:13px;color:#555;margin:12px 0 0;line-height:1.6'>"
        f"<div>{postes}</div>{ligne_reperes}</div>"
    )


def html(cfg: Config, lundi: date, planning: dict[date, Creneau], chs: list[Changement],
         aujourdhui: date, publiee: bool) -> str:
    par_jour = {ch.jour: ch for ch in chs}

    resume = ""
    if not publiee:
        items = "".join(
            f"<li style='margin:0 0 8px'><strong>{_jour(ch.jour)}</strong> "
            f"<span style='color:{COULEURS[ch.type]};font-size:13px;font-weight:600'>{ETIQUETTES[ch.type]}</span>"
            + "".join(f"<br><span style='font-size:14px'>{escape(d)}</span>" for d in details(ch))
            + "</li>"
            for ch in chs
        )
        resume = (
            "<div style='background:#fff8e1;border-left:4px solid #f0b400;padding:12px 16px;margin:0 0 16px'>"
            "<div style='font-size:13px;font-weight:700;letter-spacing:.04em;color:#7a5600;margin:0 0 8px'>CE QUI CHANGE</div>"
            f"<ul style='margin:0;padding-left:18px'>{items}</ul></div>"
        )

    lignes = []
    for i in range(7):
        j = lundi + timedelta(days=i)
        c, ch = planning.get(j), par_jour.get(j)
        surligne = ch is not None and not publiee
        fond = "#fff4cc" if surligne else "transparent"
        couleur = "#999" if j < aujourdhui else "#222"
        creneau = (f"{_pastille(POSTES[poste(c)])}<strong>{escape(_creneau(c))}</strong>"
                   if c else "<span style='color:#999;margin-left:16px'>Repos</span>")
        if c and c.intitule:
            creneau += f"<br><span style='color:#666;font-size:13px'>{escape(c.intitule)}</span>"
        if surligne and ch.avant:
            creneau += f"<br><span style='color:#b42318;font-size:13px'>avant : <s>{escape(_creneau(ch.avant))}</s></span>"
        etiquette = (
            f"<span style='color:{COULEURS[ch.type]};font-size:12px;font-weight:600'>{ETIQUETTES[ch.type]}</span>"
            if surligne else ""
        )
        lignes.append(
            f"<tr style='background:{fond};color:{couleur};border-top:1px solid #eee'>"
            f"<td style='padding:8px 12px;white-space:nowrap;vertical-align:top'>{_jour(j)}</td>"
            f"<td style='padding:8px 12px'>{creneau}</td>"
            f"<td style='padding:8px 12px;vertical-align:top;text-align:right'>{etiquette}</td></tr>"
        )
    titre = f"Semaine {lundi.isocalendar().week} · {_periode(lundi)}"
    sous_titre = "Nouvelle semaine publiée." if publiee else f"{len(chs)} jour{'s' if len(chs) > 1 else ''} modifié{'s' if len(chs) > 1 else ''}."
    return (
        "<div style='font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;color:#222;max-width:520px'>"
        f"<h2 style='font-size:18px;margin:0 0 4px'>{escape(titre)}</h2>"
        f"<p style='margin:0 0 12px;color:#666'>{sous_titre}</p>"
        + resume
        + "<table style='border-collapse:collapse;border:1px solid #e5e5e5;width:100%'>"
        + "".join(lignes)
        + "</table>"
        + legende(planning, lundi, aujourdhui, publiee)
        + "</div>"
    )


def mails(cfg: Config, planning: dict[date, Creneau], connus: dict[date, Creneau],
          changements: list[Changement], aujourdhui: date) -> list[Mail]:
    """planning : la plateforme depuis lundi ; connus : l'agenda avant ce passage."""
    resultat = []
    for lundi, chs in par_semaine(changements).items():
        publiee = _publiee(lundi, connus)
        resultat.append(Mail(
            objet(cfg, lundi, chs, publiee),
            texte(cfg, lundi, planning, chs, aujourdhui, publiee),
            html(cfg, lundi, planning, chs, aujourdhui, publiee),
        ))
    return resultat


class Messagerie:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def envoyer(self, sujet: str, texte: str, html: str | None = None) -> None:
        msg = EmailMessage()
        msg["From"] = self.cfg.smtp_user
        msg["To"] = ", ".join(self.cfg.mail_to)
        msg["Subject"] = sujet
        msg.set_content(texte)
        if html:
            msg.add_alternative(html, subtype="html")
        with smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as smtp:
            smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
            smtp.send_message(msg)

    def par_semaine(self, planning: dict[date, Creneau], connus: dict[date, Creneau],
                    changements: list[Changement], aujourdhui: date) -> None:
        for m in mails(self.cfg, planning, connus, changements, aujourdhui):
            self.envoyer(m.objet, m.texte, m.html)
